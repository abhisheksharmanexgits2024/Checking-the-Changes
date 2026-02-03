import re
import random, string
from datetime import datetime, timedelta
from tokenize import TokenError
from requests import request
import requests
from User.models import *
from .serializers import *
from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.contrib.auth.hashers import make_password
from django.template.loader import render_to_string
from django.contrib.auth.hashers import check_password
from django.core.mail import EmailMultiAlternatives
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from django.db import IntegrityError, transaction
import threading
from datetime import date, timedelta
from django.db.models import Q
from rest_framework.parsers import JSONParser
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.permissions import IsAuthenticated
from django.core.files.uploadedfile import UploadedFile

from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import TokenError

from num2words import num2words
import locale
 
locale.setlocale(locale.LC_ALL, '')

import logging
logger = logging.getLogger(__name__)



ORDER_TIMEOUT_STATUSES = [
    "new_order", "owner_review", "confirmation", "owner_declined", 
    "invoiced","invoice_processing","rider_declined"
]

ORDER_TIMEOUT_STATUSES_AGENCY = [
    "new_order", "owner_review", "confirmation", "owner_declined", 
    "invoiced","invoice_processing","rider_declined","scheduled"
]

def auto_cancel_timeout_orders():
    """
    Cancel timed-out orders and their invoices if needed.
    """
    try:
        cancelled_status = OrderStatusMaster.objects.get(order_status_name="cancelled")
        timeout_reason = CancellationReasonMaster.objects.get(reason_name="Timeout Cancelled")
        cancelled_invoice_status = InvoiceStatusMaster.objects.get(invoice_status_name="Cancelled")
    except (OrderStatusMaster.DoesNotExist, CancellationReasonMaster.DoesNotExist, InvoiceStatusMaster.DoesNotExist):
        return Response(
                    {"status": 0, "message": "Order Status or Cancellation Reason or Invoice Status Does Not Exits"},
                    status=status.HTTP_200_OK
                )

    orders = LeaseOrderMaster.objects.filter(
        order_status__order_status_name__in=ORDER_TIMEOUT_STATUSES
    )

    for order in orders:
        # Calculate remaining time
        base_time = order.created_at
        expected_timeout = base_time + timedelta(minutes=settings.ORDER_PROCESSING_TIME)
        order.remaining_time = expected_timeout

        # Timeout reached → cancel
        if timezone.now() >= expected_timeout:
            order.order_status = cancelled_status
            order.cancellation_reason = timeout_reason
            order.save()

            # vehicle = order.vehicle
            # vehicle.status = "active"
            # vehicle.save(update_fields=["status"])

            # If invoice exists for this order and is not yet cancelled, cancel it
            order_invoices = LeaseInvoice.objects.filter(lease_order=order).exclude(
                invoice_status__invoice_status_name="Cancelled"
            )
            for invoice in order_invoices:
                invoice.invoice_status = cancelled_invoice_status
                invoice.save()
        else:
            # Just update remaining_time
            order.save()

# ---------------- Auto Order and Completed Status -------

def auto_update_scheduled_orders_status():
    """
    1) SCHEDULED → ACTIVE when start_date reached (Vehicle → ON_TRIP)
    2) ACTIVE → COMPLETED when end_date passed (Vehicle → IDLE)
    Uses only DATE comparison because fields are DateField().
    """
    try:
        active_status = OrderStatusMaster.objects.get(order_status_name="active")
        completed_status = OrderStatusMaster.objects.get(order_status_name="completed")
        on_trip_status = VehicleStatusMaster.objects.get(vehicle_status_name="on_trip")
        idle_status = VehicleStatusMaster.objects.get(vehicle_status_name="idle")
    except Exception as e:
        logger.error(f"[STATUS FETCH ERROR] Required status missing: {e}")
        return

    today = timezone.now().date()

    # Update SCHEDULED → ACTIVE when start date reached
    scheduled_orders = LeaseOrderMaster.objects.filter(
        order_status__order_status_name="scheduled"
    )

    for order in scheduled_orders:
        try:
            if today >= order.start_date:       # Only date compare
                with transaction.atomic():
                    order.order_status = active_status
                    order.save()

                    vehicle = order.vehicle
                    vehicle.vehicle_status = on_trip_status
                    vehicle.save(update_fields=["vehicle_status"])

                logger.info(
                    f"[TRIP STARTED] Order {order.order_number} → ACTIVE | Vehicle {order.vehicle.plate_number} → ON_TRIP"
                )
        except Exception as e:
            logger.error(f"[ERROR STARTING ORDER] {order.lease_order_id}: {e}")

    # Update ACTIVE → COMPLETED when end date passed
    active_orders = LeaseOrderMaster.objects.filter(
        order_status__order_status_name="active"
    )

    for order in active_orders:
        try:
            if today > order.end_date:          # Strict greater
                with transaction.atomic():
                    order.order_status = completed_status
                    order.save()

                    vehicle = order.vehicle
                    vehicle.vehicle_status = idle_status
                    vehicle.save(update_fields=["vehicle_status"])

                logger.info(
                    f"[TRIP COMPLETED] Order {order.order_number} → COMPLETED | Vehicle {order.vehicle.plate_number} → IDLE"
                )
        except Exception as e:
            logger.error(f"[ERROR COMPLETING ORDER] {order.lease_order_id}: {e}")

    logger.info("[AUTO UPDATE SUCCESS] Trip start/end processing finished.")

# -----------------Vehicle License Expiry ----------------

def auto_update_vehicles_license_expiry():
    """
    Deactivate vehicles whose license has expired.
    Conditions:
      - license_expiry_date < today
      - active = False
      - vehicle_status = maintenance
    """
    pass
    # try:
    #     maintenance_status = VehicleStatusMaster.objects.get(
    #         vehicle_status_name__iexact="maintenance"
    #     )
    # except Exception as e:
    #     print(f"[ERROR] maintenance status fetch failed: {e}")
    #     return

    # today = timezone.now().date()

    # try:
    #     expired_vehicles = Vehicle_Master.objects.filter(
    #         active=True,
    #         license_expiry_date__lt=today
    #     )
    # except Exception as e:
    #     print(f"[ERROR] failed fetching expired vehicles: {e}")
    #     return

    # for vehicle in expired_vehicles:
    #     try:
    #         with transaction.atomic():
    #             vehicle.active = False
    #             vehicle.vehicle_status = maintenance_status
    #             vehicle.save(update_fields=["active", "vehicle_status"])
    #     except Exception as e:
    #         print(f"[ERROR] failed updating vehicle {vehicle.id}: {e}")

# -----------------Invoice PDF Generator Code-------------

from io import BytesIO
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from xhtml2pdf import pisa

from django.contrib.staticfiles import finders
import os

def link_callback(uri, rel):
    result = finders.find(uri.replace(settings.STATIC_URL, ""))

    if result:
        path = result
    else:
        path = os.path.join(settings.STATIC_ROOT, uri.replace(settings.STATIC_URL, ""))

    if not os.path.isfile(path):
        raise Exception('Unable to locate static file: %s' % path)

    return path
 
def format_amount(value):
    return f"{int(value):,}"

def generate_invoice_pdf(request,invoice):
    """
    Generates a PDF for the invoice and saves it in invoice.invoice_pdf.
    """
    lease_amount=invoice.lease_order.vehicle.lease_price_per_day * invoice.lease_order.total_days
    lease_order_name = f"{invoice.lease_order.vehicle.vehicle_make} {invoice.lease_order.vehicle.vehicle_model}, {invoice.lease_order.vehicle.mfg_year}"
    invoice.subtotal = int(invoice.subtotal)
    invoice.vat = int(invoice.vat)
    invoice.total_amount = int(invoice.total_amount)



    lease_amount      = format_amount(lease_amount)
    subtotal          = format_amount(invoice.subtotal)
    vat               = format_amount(invoice.vat)
    total_amount      = format_amount(invoice.total_amount)
    delivery_cost     = format_amount(invoice.delivery_cost)
    micro_insurance   = format_amount(invoice.micro_insurance)
    discount          = format_amount(invoice.discount)
    lease_price_per_day = format_amount(invoice.lease_order.vehicle.lease_price_per_day)



    Amount_In_Word = str(num2words(invoice.total_amount))
    # Render HTML

    html = render_to_string("myuser/main_invoice.html", {"invoice": invoice, "lease_amount": lease_amount, "lease_order_name": lease_order_name, "Amount_In_Word": Amount_In_Word, "subtotal": subtotal, "vat": vat, "total_amount": total_amount, "delivery_cost": delivery_cost, "micro_insurance": micro_insurance, "discount": discount, "lease_price_per_day": lease_price_per_day}, request=request)
    
    # Generate PDF in memory
    pdf_buffer = BytesIO()
    pisa.CreatePDF(html, dest=pdf_buffer,link_callback=link_callback)
 
    # Save PDF to FileField
    pdf_name = f"invoice_{invoice.invoice_number}.pdf"
    invoice.invoice_pdf.save(pdf_name, ContentFile(pdf_buffer.getvalue()), save=True)
 
    return invoice.invoice_pdf.url


# ----------------- Async email function -----------------
def send_email_async(user, password):
    try:
        subject = "Your Login Credentials"
        html_message = render_to_string('myuser/email_password.html', {'user': user, 'password': password})
        email_message = EmailMultiAlternatives(
            subject=subject,
            body="Please use the attached credentials to login.",
            from_email=settings.EMAIL_HOST_USER,
            to=[user.email]
        )
        email_message.attach_alternative(html_message, "text/html")
        email_message.send(fail_silently=True)
    except Exception as e:
        print("Email sending failed:", e)
# ----------------------------------------------------------
# -----------------------Common Email Template --------------------

def send_custom_email(to_email, subject, template_name, context={}):
    try:
        # Render HTML email template
        html_message = render_to_string(template_name, context)

        # Create the email (HTML only)
        email_message = EmailMultiAlternatives(
            subject=subject,
            body="",  # Empty plain text body
            from_email=settings.EMAIL_HOST_USER,
            to=[to_email]
        )

        # Attach HTML content
        email_message.attach_alternative(html_message, "text/html")
        email_message.send(fail_silently=True)

    except Exception as e:
        print("Email sending failed:", e)

# ------------------------------------------------------------------
EMAIL_TEMPLATES = {
    "welcome_rider": {
        "subject": "Welcome to Platform!",
        "template": "myuser/Rider/WELCOME TO VAUCH- First time.html"
    },
    "welcome_lease_agency": {
        "subject": "Welcome to Platform!",
        "template": "myuser/Lease Agent/Welcome to Vauch — Let’s Get You Fully Onboarded.html"
    },
    "welcome_car_owner": {
        "subject": "Welcome to Platform!",
        "template": "myuser/Car Owner/Welcome to Vauch Let’s Get You Fully Onboarded.html"
    },
    "new_request": {
        "subject": "New Request Coming!",
        "template": "myuser/Lease Agent/New Lease Request.html"
    },
    "cancel_lease_agency": {
        "subject": "Your Rider Has Been Cancel",
        "template": "myuser/Rider/Update - Your Vauch Booking Has Been Cancelled by Agent.html"
    },
    "order_confirmation": {
        "subject": "Confirmation Order Request",
        "template": "myuser/Car Owner/New Lease Confirmation Request -  Lease Agency.html"
    },
    
    "invoice_ready": {
        "subject": "Invoice for Your Recent Order!",
        "template": "myuser/Rider/Your Vauch Invoice Is Ready.html"
    },
    "invoice_template": {
        "subject": "Complete Your Booking Payment",
        "template": "myuser/Rider/Invoice Template.html"
    },
    "payment_confirm": {
        "subject": "Your Vauch Trip is Scheduled",
        "template": "myuser/Rider/Payment Confirmed — Your Vauch Trip Is Scheduled.html"
    },
}

def send_email(email_type, to_email, context):
    context['url'] = "https://www.vauchapp.com"
    data = EMAIL_TEMPLATES.get(email_type)

    if not data:
        print("Invalid email type:", email_type)
        return

    threading.Thread(
        target=send_custom_email,
        args=(to_email, data["subject"], data["template"], context)
    ).start()
# ------------------------------------------------------------------

DUMMY_NIN_RESPONSE = {
    "nin_number": "88295185413",
    "role":"lease_agency",
    "first_name": "tarangi",
    "last_name": "sharma",
    "gender": "Male",
    "middle_name": "",
    "date_of_birth": "2000-01-01",
    "email": "test11@example.com",
    "phone_number": "9874563214",
    "employment_status": "employment",
    "marital_status": "mingal",
    "photo": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAYAAADgdz34AAAABHNCSVQICAgIfAhkiAAAAAlwSFlzAAAApgAAAKYB3X3/OAAAABl0RVh0U29mdHdhcmUAd3d3Lmlua3NjYXBlLm9yZ5vuPBoAAANCSURBVEiJtZZPbBtFFMZ/M7ubXdtdb1xSFyeilBapySVU8h8OoFaooFSqiihIVIpQBKci6KEg9Q6H9kovIHoCIVQJJCKE1ENFjnAgcaSGC6rEnxBwA04Tx43t2FnvDAfjkNibxgHxnWb2e/u992bee7tCa00YFsffekFY+nUzFtjW0LrvjRXrCDIAaPLlW0nHL0SsZtVoaF98mLrx3pdhOqLtYPHChahZcYYO7KvPFxvRl5XPp1sN3adWiD1ZAqD6XYK1b/dvE5IWryTt2udLFedwc1+9kLp+vbbpoDh+6TklxBeAi9TL0taeWpdmZzQDry0AcO+jQ12RyohqqoYoo8RDwJrU+qXkjWtfi8Xxt58BdQuwQs9qC/afLwCw8tnQbqYAPsgxE1S6F3EAIXux2oQFKm0ihMsOF71dHYx+f3NND68ghCu1YIoePPQN1pGRABkJ6Bus96CutRZMydTl+TvuiRW1m3n0eDl0vRPcEysqdXn+jsQPsrHMquGeXEaY4Yk4wxWcY5V/9scqOMOVUFthatyTy8QyqwZ+kDURKoMWxNKr2EeqVKcTNOajqKoBgOE28U4tdQl5p5bwCw7BWquaZSzAPlwjlithJtp3pTImSqQRrb2Z8PHGigD4RZuNX6JYj6wj7O4TFLbCO/Mn/m8R+h6rYSUb3ekokRY6f/YukArN979jcW+V/S8g0eT/N3VN3kTqWbQ428m9/8k0P/1aIhF36PccEl6EhOcAUCrXKZXXWS3XKd2vc/TRBG9O5ELC17MmWubD2nKhUKZa26Ba2+D3P+4/MNCFwg59oWVeYhkzgN/JDR8deKBoD7Y+ljEjGZ0sosXVTvbc6RHirr2reNy1OXd6pJsQ+gqjk8VWFYmHrwBzW/n+uMPFiRwHB2I7ih8ciHFxIkd/3Omk5tCDV1t+2nNu5sxxpDFNx+huNhVT3/zMDz8usXC3ddaHBj1GHj/As08fwTS7Kt1HBTmyN29vdwAw+/wbwLVOJ3uAD1wi/dUH7Qei66PfyuRj4Ik9is+hglfbkbfR3cnZm7chlUWLdwmprtCohX4HUtlOcQjLYCu+fzGJH2QRKvP3UNz8bWk1qMxjGTOMThZ3kvgLI5AzFfo379UAAAAASUVORK5CYII="" ",
}

DUMMY_CAC_RESPONSE ={
     
                "business_name": "AutoLease Nigeria Ltd",
                "business_email": "info@autolease.ng",
                "business_number": "07012345678",
                "business_type": "Vehicle Leasing",
                "year": "2015",
                "state": "Lagos",
                "company_name": "AutoLease Nigeria Ltd"
}

# ----------------- GENERATE PASSWORD FUNCTION -----------------
def generate_valid_password(length=8):
    specials = "!@#$"

    while True:
        # Generate random password
        password = ''.join(
            random.choices(string.ascii_letters + string.digits + specials, k=length)
        )

        # Validation checks
        if (len(password) >= 8
            and re.search(r'[A-Z]', password)
            and re.search(r'\d', password)
            and re.search(r'[!@#$&?]', password)):
            return password
        
# ------------------------ CALCUATE DISTANCE Function --------------------------
import urllib.parse
def get_distance_km(origin, destination):
    """
    Calculate distance between two addresses using Google Distance Matrix API.
    Returns distance in KM or fallback 10 KM if API key is missing.
    """
 
    # Fallback if API key is missing
    if not settings.GOOGLE_MAPS_API_KEY:
        return 0
    
    GOOGLE_MAPS_API_KEY = settings.GOOGLE_MAPS_API_KEY
    try:
        # Encode addresses safely
        origin_enc = urllib.parse.quote_plus(origin)
        dest_enc = urllib.parse.quote_plus(destination)
 
        url = (
            f"https://maps.googleapis.com/maps/api/distancematrix/json"
            f"?origins={origin_enc}&destinations={dest_enc}"
            f"&units=metric&key={GOOGLE_MAPS_API_KEY}"
        )
 
        response = requests.get(url)
        data = response.json()
 
        # API-level errors
        if data.get("status") != "OK":
            return 0
 
        element = data["rows"][0]["elements"][0]
 
        if element.get("status") != "OK":
            return 0
 
        distance_meters = element["distance"]["value"]
        distance = distance_meters / 1000
        return int(distance * 10) / 10  # Convert to KM
 
    except Exception as e:
        print("Error:", e)
        return 0

# ===================================     NIN API CALLING    ======================================= #

class VerifyNINAPI(APIView):
    @swagger_auto_schema(
        operation_description="Verify user NIN and fetch user details",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                "nin_number": openapi.Schema(type=openapi.TYPE_STRING, description="User NIN number")
            },
            required=["nin_number"]
        ),
        responses={
            1: openapi.Response(description="NIN verified successfully"),
            0: openapi.Response(description="Failed to verify NIN"),
            2: openapi.Response(description="Verification service unavailable")
        }
    )
    def post(self, request):
        nin_number = request.data.get("nin_number")
        if not nin_number:
            return Response({"status": 0, "message": "NIN number is required", "data": None}, status=200)

        use_mock = getattr(settings, "USE_NIN_MOCK", True)
        if use_mock:
            nin_data = DUMMY_NIN_RESPONSE
        else:
            try:
                base_url = settings.NIN_BASE_URL
                headers = {
                    "AppId": settings.APP_ID,
                    "Authorization": f"{settings.PRODUCTION_SECRET_KEY}",
                }
                response = requests.get(base_url, headers=headers, params={"nin": nin_number})
                response.raise_for_status()
                nin_data = response.json()
                nin_data["nin"] = nin_number
            except requests.RequestException as e:
                return Response({
                    "status": 0,
                    "message": "Failed to verify NIN",
                    "error": str(e),
                    "data": None
                }, status=200)

        # Handle nested structure like nin_data["entity"]
        entity = nin_data.get("entity", {})
        entity["nin_number"] = nin_number

        serializer = NINResponseSerializer(entity, context={"request": request})
        formatted_data = serializer.data

        return Response({
            "status": 1,
            "message": "NIN verified successfully",
            "nin_data": formatted_data
        }, status=200)
        
# ===================================     CAC API CALLING    ======================================= #

class VerifyCACAPI(APIView):
    @swagger_auto_schema(
        operation_description="Verify company CAC number and fetch company details",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                "cac_number": openapi.Schema(type=openapi.TYPE_STRING, description="Company CAC number")
            },
            required=["cac_number"]
        ),
        responses={
            1: openapi.Response(description="CAC verified successfully"),
            0: openapi.Response(description="Failed to verify CAC"),
            0: openapi.Response(description="Verification service unavailable")
        }
    )
    def get_qoreid_token(self):
        """Fetch fresh access token from QoreID"""
        url = f"{settings.QOREID_BASE_URL}/token"
        headers = {"Content-Type": "application/json"}
        payload = {
            "clientId": settings.QOREID_CLIENT_ID,
            "secret": settings.QOREID_SECRET
        }

        try:
            response = requests.post(url, json=payload, headers=headers)
            if response.status_code == 201:
                data = response.json()
                return data.get("accessToken")
            else:
                return None
        except requests.exceptions.RequestException:
            return None

    def post(self, request):

        cac_number = request.data.get("cac_number")

        if not cac_number:
            return Response({"status": 0, "message": "CAC number is required"}, status=200)

        use_mock = getattr(settings, "USE_CAC_MOCK")

        if use_mock:
            cac_data = {
                "cac_number": cac_number,
                "business_name": " ",
                "business_email": "",
                "business_number": "",
                "business_type": " ",
                "year": "",
                "state": "",
                "company_name": "  ",
            }
            return Response({"status":1, "message":" user mock data successfully verifay ", "data":cac_data})

        else:
            try:
                token = self.get_qoreid_token()
                if not token:
                    return Response({
                        "status": 0,
                        "message": "Failed to get QoreID access token."
                    }, status=200)
                
                url = f"{settings.QOREID_BASE_URL}/v1/ng/identities/cac-basic"
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"
                }

                payload = {"regNumber":cac_number}
                response = requests.post(url, headers=headers, json=payload)
                if response.status_code == 200:                    	
                    # Get full API response
                    full_response = response.json()
        
                    cac = full_response.get("cac", {})
                    
                    business_data = {
                        "cac_number": cac_number,
                        "full_name":"",
                        "business_name": cac.get("companyName"),
                        "business_email": cac.get("companyEmail"),
                        "business_number": "",
                        "business_type": cac.get("companyType"),
                        "year": cac.get("registrationDate"),
                        "state": cac.get("state"),
                        "company_name": cac.get("companyName"),
                        "address": cac.get("headOfficeAddress"),
                    }

                    return Response({"status": 1, "message": "CAC verified successfully","data":{"full_response": full_response, "business_data": business_data }}, status=200)
                else:
                    return Response({
                        "status": 0,
                        "message": "Verification failed",
                        "error": response.text
                    }, status=200)
            except requests.RequestException as e:
                        return Response({
                            "status": 0,
                            "message": "Failed to verify CAC",
                            "error": str(e)
                        }, status=200)
            except Exception as e:
                return Response({"status": 0, "message":"internal server error", "error":str(e),"data":None}, status=200)

# class VerifyCACAPI(APIView):
#     @swagger_auto_schema(
#         operation_description="Verify company CAC number and fetch company details",
#         request_body=openapi.Schema(
#             type=openapi.TYPE_OBJECT,
#             properties={
#                 "cac_number": openapi.Schema(type=openapi.TYPE_STRING, description="Company CAC number")
#             },
#             required=["cac_number"]
#         ),
#         responses={
#             1: openapi.Response(description="CAC verified successfully"),
#             0: openapi.Response(description="Failed to verify CAC"),
#             0: openapi.Response(description="Verification service unavailable")
#         }
#     )
#     def get_qoreid_token(self):
#         """Fetch fresh access token from QoreID"""
#         url = f"{settings.QOREID_BASE_URL}/token"
#         headers = {"Content-Type": "application/json"}
#         payload = {
#             "clientId": settings.QOREID_CLIENT_ID,
#             "secret": settings.QOREID_SECRET
#         }

#         try:
#             response = requests.post(url, json=payload, headers=headers)
#             if response.status_code == 201:
#                 data = response.json()
#                 return data.get("accessToken")
#             else:
#                 return None
#         except requests.exceptions.RequestException:
#             return None
#     def post(self, request):
#         cac_number = request.data.get("cac_number")

#         if not cac_number:
#             return Response({"status": 0, "message": "CAC number is required"}, status=200)

#         use_mock = getattr(settings, "USE_CAC_MOCK")

#         if use_mock:
#             cac_data = {
#                 "cac_number": cac_number,
#                 "business_name": " ",
#                 "business_email": "",
#                 "business_number": "",
#                 "business_type": " ",
#                 "year": "",
#                 "state": "",
#                 "company_name": "  ",
#             }
#             return Response({"status":1, "message":" user mock data successfully verifay ", "data":cac_data})

#         else:
#             try:
#                 base_url = settings.CAC_BASE_URL
#                 headers = {
#                     "AppId": settings.APP_ID,
#                     "Authorization": f"{settings.PRODUCTION_SECRET_KEY}",
#                 }
#                 response = requests.get(base_url, headers=headers, params={"rc_number": cac_number})
#                 response.raise_for_status()

#                 # Get full API response
#                 full_response = response.json()
    
#                 entity = full_response.get("entity", {})
#                 affiliates = entity.get("affiliates", [{}])
#                 first_affiliates = affiliates[0] if affiliates else {}
                
#                 full_name = f"{first_affiliates.get('first_name')} {first_affiliates.get('last_name')}"

#                 business_data = {
#                     "cac_number": cac_number, # entity.get("rc_number"),
#                     "full_name":full_name,
#                     "business_name": entity.get("company_name"),
#                     "business_email": entity.get("email"),
#                     "business_number": first_affiliates.get("phone_number"),
#                     "business_type": entity.get("type_of_company"),
#                     "year": entity.get("date_of_registration"),
#                     "state": entity.get("state"),
#                     "company_name": entity.get("company_name"),
#                     "address": entity.get("address"),
#                 }
        
#                 return Response({"status": 1, "message": "CAC verified successfully","data":{"full_response": full_response, "business_data": business_data }}, status=200)

#             except requests.RequestException as e:
#                         return Response({
#                             "status": 0,
#                             "message": "Failed to verify CAC",
#                             "error": str(e)
#                         }, status=200)
#             except Exception as e:
#                 return Response({"status": 0, "message":"internal server error", "error":str(e),"data":None}, status=200)

# =================================    CREATE USER TYPE API    ==================================== #

class VerifyPlateNumber(APIView):

    def get_qoreid_token(self):
        """Fetch fresh access token from QoreID"""
        url = f"{settings.QOREID_BASE_URL}/token"
        headers = {"Content-Type": "application/json"}
        payload = {
            "clientId": settings.QOREID_CLIENT_ID,
            "secret": settings.QOREID_SECRET
        }

        try:
            response = requests.post(url, json=payload, headers=headers)
            if response.status_code == 201:
                data = response.json()
                return data.get("accessToken")
            else:
                return None
        except requests.exceptions.RequestException:
            return None

    def post(self, request):
        plate_number = request.data.get("plate_number")
        # firstname = request.data.get("firstname")
        # lastname = request.data.get("lastname")

        # Validation
        if not all([plate_number]):
            return Response({
                "status": 0,
                "message": "plate_number are required."
            }, status=200)

        exists = Vehicle_Master.objects.filter(plate_number__iexact=plate_number).exists()

        if exists:
            return Response({
                "status": 0,
                "message": "Plate number already registered",
            },status=200)
        # Get Token
        token = self.get_qoreid_token()
        if not token:
            return Response({
                "status": 0,
                "message": "Failed to get QoreID access token."
            }, status=200)

        # Call QoreID API
        url = f"{settings.QOREID_BASE_URL}/v1/ng/identities/license-plate-premium/{plate_number}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        try:
            response = requests.post(url, headers=headers)
            if response.status_code == 200:
                return Response({
                    "status": 1,
                    "message": "Plate number verified successfully",
                    "data": response.json()
                }, status=200)
            else:
                return Response({
                    "status": 0,
                    "message": "Verification failed",
                    "error": response.text
                }, status=200)
        except requests.exceptions.RequestException as e:
            return Response({
                "status": 0,
                "message": "Error connecting to QoreID API",
                "error": str(e)
            }, status=200)
            

# =================================    CREATE USER TYPE API    ==================================== #

class UserTypeCreateAPI(APIView):
    def get(self, request):
       
        user_types = User_Type.objects.all()
       
        serializer = UserTypeSerializer(user_types, many=True)
       
        return Response({"status": 1,"message": "User types fetched successfully","data": serializer.data}, status=status.HTTP_200_OK)

    def post(self, request):
        
        serializer = UserTypeSerializer(data=request.data)
        
        if serializer.is_valid():
            serializer.save()
           
            return Response({"status": 1,"message": "User type created successfully","data": serializer.data}, status=200)
        
        return Response({"status": 0,"message": "Invalid data","errors": serializer.errors,"data":None}, status=200)


# =========================================   REGISTER WITH USER TYPE API  ========================================= #

class RegisterAPI(APIView):
    @swagger_auto_schema(
        operation_description="Register a new user (Rider, Lease Agency, or Vehicle Owner)",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                "nin_data": openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    description="Verified NIN response data used for identity verification",
                    properties={
                        "nin_number": openapi.Schema(type=openapi.TYPE_STRING, example="70123456789"),
                        "first_name": openapi.Schema(type=openapi.TYPE_STRING, example="John"),
                        "last_name": openapi.Schema(type=openapi.TYPE_STRING, example="Adamu"),
                        "middle_name": openapi.Schema(type=openapi.TYPE_STRING, example="Doe"),
                        "gender": openapi.Schema(type=openapi.TYPE_STRING, example="M"),
                        "phone_number": openapi.Schema(type=openapi.TYPE_STRING, example="08011111111"),
                        "date_of_birth": openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATE, example="1990-01-01"),
                        "photo": openapi.Schema(type=openapi.TYPE_STRING, example="http://example.com/media/users/john.jpg"),
                        "customer": openapi.Schema(type=openapi.TYPE_STRING, example="6bb82c41-e15e-4308-b99d-e9640818eca9"),
                    },
                    required=["nin_number", "first_name", "last_name", "gender", "date_of_birth"]
                ),
                "email": openapi.Schema(type=openapi.TYPE_STRING),
                "phone_number": openapi.Schema(type=openapi.TYPE_STRING),
                "user_type": openapi.Schema(type=openapi.TYPE_STRING),

                # FILE UPLOAD ONLY
                "agency_profile": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    format=openapi.FORMAT_BINARY,
                    description="Lease Agency profile image file"
                ),

                "business_data": openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "cac_number": openapi.Schema(type=openapi.TYPE_STRING, example="CAC123456"),
                        "business_name": openapi.Schema(type=openapi.TYPE_STRING, example="Adamu Logistics Ltd."),
                        "business_email": openapi.Schema(type=openapi.TYPE_STRING, example="info@adamulogistics.com"),
                        "business_number": openapi.Schema(type=openapi.TYPE_STRING, example="08055555555"),
                        "business_type": openapi.Schema(type=openapi.TYPE_STRING, example="Transport"),
                        "phone_number": openapi.Schema(type=openapi.TYPE_STRING, example="08099999999"),
                        "year": openapi.Schema(type=openapi.TYPE_STRING, example="2018"),
                        "state": openapi.Schema(type=openapi.TYPE_STRING, example="Lagos"),
                        "company_name": openapi.Schema(type=openapi.TYPE_STRING, example="Adamu Logistics"),
                        "full_name": openapi.Schema(type=openapi.TYPE_STRING, example="John Adamu"),
                        "address": openapi.Schema(type=openapi.TYPE_STRING, example="12 Adeola Street, Ikeja, Lagos"),
                    },
                ),
                "contact_info": openapi.Schema(
                    type=openapi.TYPE_ARRAY,
                    items=openapi.Schema(
                        type=openapi.TYPE_OBJECT,
                        properties={
                            "owner_name": openapi.Schema(type=openapi.TYPE_STRING, example="Adamu Support Center"),
                            "email": openapi.Schema(type=openapi.TYPE_STRING, example="support@adamulogistics.com"),
                            "phone_number": openapi.Schema(type=openapi.TYPE_STRING, example="08033334444"),
                        },
                        required=["email", "phone_number"]
                    )
                ),
            },
            required=["nin_data", "email", "phone_number", "user_type"]
        )
    )
    def post(self, request):
        try:
            with transaction.atomic():
                nin_data = request.data.get("nin_data")
                email = request.data.get("email")
                phone_number = request.data.get("phone_number")
                user_type_input = request.data.get("user_type")
                business_data = request.data.get("business_data")
                contact_info = request.data.get("contact_info")
                driver_info = request.data.get("driver_info")

                # --- Validation ---
                if not nin_data:
                    return Response({"status": 0, "message": "NIN data is required", "data": None}, status=200)
                if not user_type_input:
                    return Response({"status": 0, "message": "User type is required", "data": None}, status=200)

                # --- Resolve user type ---
                try:
                    user_type_obj = User_Type.objects.get(user_type_name__iexact=user_type_input)
                except User_Type.DoesNotExist:
                    return Response({"status": 0, "message": f"Invalid user type name '{user_type_input}'", "data": None}, status=200)

                # --- Extract NIN number ---
                nin_number = nin_data.get("nin") or nin_data.get("nin_number")
                if not nin_number:
                    return Response({"status": 0, "message": "NIN number missing", "data": None}, status=200)

                # --- Duplicate checks ---
                if User_Master.objects.filter(email=email).exists():
                    return Response({"status": 0, "message": "User with this email already exists", "data": None}, status=200)
                # if User_Master.objects.filter(phone_number=phone_number).exists():
                #     return Response({"status": 0, "message": "User with this phone number already exists", "data": None}, status=200)

                # --- Create user from NIN data ---
                user_data = {**nin_data, "nin_number": nin_number, "email": email, "phone_number": phone_number}
                serializer = UserSerializers(data=user_data, context={"request": request})
                if not serializer.is_valid():
                    return Response({
                        "status": 0,
                        "message": "Invalid user data",
                        "errors": serializer.errors,
                        "data": None
                    }, status=200)

                user = serializer.save(user_type=user_type_obj)

                nin_photo = nin_data.get("photo")
                # if nin_photo:
                #     try:
                #         # Already saved in MEDIA folder (from NIN response)
                #         if nin_photo.startswith("http") and "/media/" in nin_photo:
                #             relative_path = nin_photo.split(settings.MEDIA_URL)[-1]
                #             user.photo.name = relative_path
                #             user.save()

                #         # : External or base64 photo
                #         elif nin_photo.startswith(("http", "/9j/", "iVBOR", "R0lGOD")):
                #             if nin_photo.startswith("http"):
                #                 response = requests.get(nin_photo)
                #                 if response.status_code == 200:
                #                     file_name = f"nin_{uuid.uuid4()}.jpg"
                #                     user.photo.save(file_name, ContentFile(response.content), save=True)
                #             else:
                #                 image_data = base64.b64decode(nin_photo)
                #                 file_name = f"nin_{uuid.uuid4()}.jpg"
                #                 user.photo.save(file_name, ContentFile(image_data), save=True)

                #     except Exception as e:
                #         print("⚠️ Failed to save or assign NIN photo:", e)
                if nin_photo and nin_photo.startswith("http") and "/media/" in nin_photo:
                    relative_path = nin_photo.split(settings.MEDIA_URL)[-1]
                    user.photo.name = relative_path
                    user.save()
                
                password = generate_valid_password(8)
                user.password = make_password(password)
                user.save()

                business_response = None
                contact_data = None
                driver_data = None

                # --- Handle Business Info ---
                if user_type_obj.user_type_name in ["LeaseAgency", "Owner"]:
                    if not business_data:
                        return Response({"status": 0, "message": "Business data required for this user type", "data": None}, status=200)

                    try:
                        cac_year = int(datetime.fromisoformat(business_data.get("year").replace("Z", "+00:00")).year)
                    except:
                        cac_year = None

                    if user_type_obj.user_type_name == "LeaseAgency":

                        lease_data = Lease_Agency_Master.objects.create(
                            user_id=user,
                            cac_number=business_data.get("cac_number"),
                            business_name=business_data.get("business_name"),
                            business_Email=business_data.get("business_email"),
                            business_number=business_data.get("business_number"),
                            business_type=business_data.get("business_type"),
                            phone_number=business_data.get("phone_number"),
                            year=cac_year,
                            state=business_data.get("state"),
                            company_name=business_data.get("company_name"),
                            full_name=f"{user.first_name} {user.last_name}",
                            address=business_data.get("address")
                        )

                        # if client sends URL instead of file
                        agency_profile_url = request.data.get("agency_profile")
                        if agency_profile_url and agency_profile_url.startswith("http"):
                            try:
                                # convert absolute URL to relative path
                                relative_path = agency_profile_url.split(settings.MEDIA_URL)[-1]
                                lease_data.agency_profile.name = relative_path
                                lease_data.save()
                            except:
                                pass

                        business_response = LeaseAgencySerializer(lease_data, context={"request": request}).data

                        business_response.pop("contact_infos", None)

                    else:
                        owner_data = Vehicle_Owner_Master.objects.create(
                            user_id=user,
                            cac_number=business_data.get("cac_number"),
                            business_name=business_data.get("business_name"),
                            business_Email=business_data.get("business_email"),
                            business_number=business_data.get("business_number"),
                            business_type=business_data.get("business_type"),
                            phone_number=business_data.get("phone_number"),
                            year=cac_year,
                            company_name=business_data.get("company_name"),
                            state=business_data.get("state"),
                            full_name=f"{user.first_name} {user.last_name}",
                            address=business_data.get("address"),
                        )

                        agency_id = business_data.get("agency_id")
                        if agency_id:
                            try:
                                agency_obj = Lease_Agency_Master.objects.get(id=agency_id)
                                owner_data.agency = agency_obj
                                owner_data.save()
                            except Lease_Agency_Master.DoesNotExist:
                                pass
                            
                        business_response = {
                            "id": str(owner_data.id),
                            "business_name": owner_data.business_name,
                            "state": owner_data.state,
                            "business_email": owner_data.business_Email,
                        }

                        driver_list=[]
                        if driver_info and isinstance(driver_info, list):
                            for d in driver_info:
                                driver = Vehicle_Owner_Driver.objects.create(
                                    vehicle_owner=owner_data,
                                    name=d.get("name"),
                                    email=d.get("email"),
                                    phone_number=int(d.get("phone_number"))
                                )
                                driver_list.append({
                                    "id": str(driver.id),
                                    "name": driver.name,
                                    "email": driver.email,
                                    "phone_number": driver.phone_number
                                })
                            driver_data = driver_list    
                #  Contact Info
                if contact_info:
                    if isinstance(contact_info, list):
                        contact_list = []
                        for info in contact_info:
                            c = ContactInfo.objects.create(
                                user=user,
                                owner_name=info.get("owner_name"),
                                email=info.get("email"),
                                phone_number=info.get("phone_number")
                            )
                            contact_list.append({
                                "id": str(c.id),
                                "owner_name": c.owner_name,
                                "email": c.email,
                                "phone_number": c.phone_number
                            })
                        contact_data = contact_list

                # Send Email
                threading.Thread(target=send_email_async, args=(user, password)).start()
                
                if user_type_obj.user_type_name == "LeaseAgency":
                    send_email(
                        email_type="welcome_lease_agency",
                        to_email=user.email,
                        context={"user": user}
                    )
                if user_type_obj.user_type_name == "Rider":
                    send_email(
                        email_type="welcome_rider",
                        to_email=user.email,
                        context={"user": user}
                    )
                if user_type_obj.user_type_name == "Owner":
                    send_email(
                        email_type="welcome_car_owner",
                        to_email=user.email,
                        context={"user": user}
                    )

                # Make agency_profile full URL if exists
                if business_response and isinstance(business_response, dict):
                    if business_response.get("agency_profile") and not business_response["agency_profile"].startswith("http"):
                        business_response["agency_profile"] = request.build_absolute_uri(business_response["agency_profile"])

                # Success Response
                return Response({
                    "status": 1,
                    "message": f"{user_type_obj.user_type_name} registration successful. Login credentials sent to email.",
                    "data": {
                        "user_id": str(user.id),
                        "photo": request.build_absolute_uri(user.photo.url) if user.photo else None,
                        "first_name": user.first_name,
                        "last_name": user.last_name,
                        "email": user.email,
                        "phone_number": user.phone_number,
                        "user_type": user.user_type.user_type_name,
                        "business_data": business_response,
                        "contact_info": contact_data,
                        "driver_info": driver_data
                    }
                }, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong during registration",
                "error": str(e),
                "data": None
            }, status=200)
            

class VerifyAPI(APIView):

    @swagger_auto_schema(
        operation_description="Check if an email already exists in the system.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'email': openapi.Schema(type=openapi.TYPE_STRING, description='User email'),
            },
            required=[]  # Either field can be sent
        ),
        responses={200: openapi.Response(description="Verification result")}
    )
    def post(self, request):
        email = request.data.get("email")

        # Validation: Require at least one field
        if not email:
            return Response({
                "status": 0,
                "message": "Please provide an email.",
                "data": None
            }, status=200)

        # Independent checks
        email_exists = User_Master.objects.filter(email__iexact=email).exists() if email else False

        # Only email exists
        if email_exists:
            return Response({
                "status": 0,
                "message": "Email already exists.",
                "data": {"email": email}
            }, status=status.HTTP_200_OK)

        # Both available — respond more clearly depending on input
        if email:
            msg = "Email is available."

        return Response({
            "status": 1,
            "message": msg,
            "data": None
        }, status=status.HTTP_200_OK)

            
# class GetAllUserAPI(APIView):
#     permission_classes = [IsAuthenticated]

#     @swagger_auto_schema(
#         security=[{'Bearer': []}],
#         operation_description="Fetch all users from User_Master, or a single user by user_id.",
#         manual_parameters=[
#             openapi.Parameter(
#                 "user_id",
#                 openapi.IN_QUERY,
#                 description="UUID of a specific user (optional)",
#                 type=openapi.TYPE_STRING,
#                 required=False
#             ),
#         ],
#         responses={
#             200: openapi.Response(description="Users fetched successfully"),
#             404: openapi.Response(description="User not found")
#         }
#     )
#     def get(self, request):
#         user_id = request.query_params.get("user_id")

#         # Fetch single user if user_id is provided
#         if user_id:
#             try:
#                 user = User_Master.objects.get(id=user_id)
#                 user_data = {
#                     "id": str(user.id),
#                     "first_name": user.first_name,
#                     "last_name": user.last_name,
#                     "email": user.email,
#                     "phone_number": user.phone_number,
#                     "user_type": user.user_type.user_type_name if user.user_type else None,
#                 }
#                 return Response({
#                     "status": 1,
#                     "message": "User fetched successfully",
#                     "data": user_data
#                 }, status=200)
#             except User_Master.DoesNotExist:
#                 return Response({
#                     "status": 0,
#                     "message": "User not found",
#                     "data": None
#                 }, status=404)

#         # Fetch all users
#         users = User_Master.objects.all()
#         if not users.exists():
#             return Response({
#                 "status": 0,
#                 "message": "No users found",
#                 "data": None
#             }, status=404)

#         user_list = [{
#             "id": str(user.id),
#             "first_name": user.first_name,
#             "last_name": user.last_name,
#             "email": user.email,
#             "phone_number": user.phone_number,
#             "user_type": user.user_type.user_type_name if user.user_type else None,
#         } for user in users]

#         return Response({
#             "status": 1,
#             "message": "Users fetched successfully",
#             "data": user_list
#         }, status=200)

# class GetAllUserAPI(APIView):
#     permission_classes = [IsAuthenticated]

#     @swagger_auto_schema(
#         security=[{'Bearer': []}],
#         operation_description="Fetch details of the logged-in user using the JWT token.",
#         responses={
#             1: openapi.Response(description="User fetched successfully"),
#             0: openapi.Response(description="Unauthorized or invalid token"),
#         }
#     )

#     def get(self, request):
#         user = request.user  # Extract user from JWT token

#         if not user or not hasattr(user, "id"):
#             return Response({
#                 "status": 0,
#                 "message": "Invalid or missing authentication token",
#                 "data": None
#             }, status=200)

#         # Handle photo safely (return full URL if available)
#         photo_url = None
#         if user.photo and hasattr(user.photo, "url"):
#             photo_url = request.build_absolute_uri(user.photo.url)

#         # Build user data
#         user_data = {
#             "id": str(user.id),
#             "photo": photo_url,
#             "first_name": user.first_name,
#             "last_name": user.last_name,
#             "email": user.email,
#             "phone_number": user.phone_number,
#             "user_type": user.user_type.user_type_name if user.user_type else None,
#         }

#         return Response({
#             "status": 1,
#             "message": "User fetched successfully",
#             "data": user_data
#         }, status=200)


class GetUserProfileAPI(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        security=[{'Bearer': []}],
        operation_description="Fetch full profile of the logged-in user (Rider, LeaseAgency, Owner).",
        responses={
            1: openapi.Response(description="Profile fetched"),
            0: openapi.Response(description="Unauthorized or invalid user"),
        }
    )
    def get(self, request):
        user = request.user

        # Check token validity
        if not user or not hasattr(user, "id"):
            return Response({
                "status": 0,
                "message": "Invalid or missing authentication token",
                "data": None
            }, status=200)

        user_type = user.user_type.user_type_name if user.user_type else None

        # User Photo
        photo_url = request.build_absolute_uri(user.photo.url) if user.photo else None

        # Base Response Structure
        profile_data = {
            "user": {
                "id": str(user.id),
                "photo": photo_url,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "email": user.email,
                "phone_number": user.phone_number,
                "user_type": user_type,
            }
        }

        # -------------------------
        # RIDER
        # -------------------------
        if user_type == "Rider":
            profile_data["business_data"] = None
            profile_data["driver_info"] = None
            profile_data["contact_info"] = None
            return Response({
                "status": 1,
                "message": "Rider profile fetched successfully",
                "data": profile_data
            }, status=200)

        # -------------------------
        # LEASE AGENCY
        # -------------------------
        if user_type == "LeaseAgency":
            try:
                agency = Lease_Agency_Master.objects.get(user_id=user)
            except Lease_Agency_Master.DoesNotExist:
                return Response({
                    "status": 0,
                    "message": "Lease Agency business profile not found",
                    "data": None
                }, status=200)

            agency_profile = (
                request.build_absolute_uri(agency.agency_profile.url)
                if agency.agency_profile else None
            )

            profile_data["business_data"] = {
                "id": str(agency.id),
                "cac_number": agency.cac_number,
                "business_name": agency.business_name,
                "business_email": agency.business_Email,
                "business_number": agency.business_number,
                "business_type": agency.business_type,
                "phone_number": agency.phone_number,
                "year": agency.year,
                "state": agency.state,
                "company_name": agency.company_name,
                "full_name": agency.full_name,
                "address": agency.address,
                "agency_profile": agency_profile,
            }

            profile_data["driver_info"] = None
            profile_data["contact_info"] = list(
                ContactInfo.objects.filter(user=user).values(
                    "id", "owner_name", "email", "phone_number"
                )
            )

            return Response({
                "status": 1,
                "message": "Lease Agency profile fetched successfully",
                "data": profile_data
            }, status=200)

        # -------------------------
        # OWNER
        # -------------------------
        if user_type == "Owner":
            try:
                owner = Vehicle_Owner_Master.objects.get(user_id=user)
            except Vehicle_Owner_Master.DoesNotExist:
                return Response({
                    "status": 0,
                    "message": "Owner business profile not found",
                    "data": None
                }, status=200)

            # Drivers
            driver_list = Vehicle_Owner_Driver.objects.filter(vehicle_owner=owner).values(
                "id", "name", "email", "phone_number"
            )

            # Agency Details (if mapped)
            agency_data = None
            if owner.agency:
                agency_data = {
                    "id": str(owner.agency.id),
                    "business_name": owner.agency.business_name,
                    "business_email": owner.agency.business_Email,
                    "business_number": owner.agency.business_number,
                    "business_type": owner.agency.business_type,
                    "address": owner.agency.address,
                    "state": owner.agency.state,
                }

            # Complete Owner Business Block
            profile_data["business_data"] = {
                "id": str(owner.id),
                "cac_number": owner.cac_number,
                "full_name": owner.full_name,
                "business_name": owner.business_name,
                "business_email": owner.business_Email,
                "business_number": owner.business_number,
                "business_type": owner.business_type,
                "phone_number": owner.phone_number,
                "year": owner.year,
                "state": owner.state,
                "address": owner.address,
                "company_name": owner.company_name,
                # Newly added
                "agency": agency_data,
                "name_of_bank": owner.name_of_bank,
                "account_name": owner.account_name,
                "account_number": owner.account_number,
            }

            profile_data["driver_info"] = list(driver_list)

            profile_data["contact_info"] = list(
                ContactInfo.objects.filter(user=user).values(
                    "id", "owner_name", "email", "phone_number"
                )
            )

            return Response({
                "status": 1,
                "message": "Owner profile fetched successfully",
                "data": profile_data
            }, status=200)

        # -------------------------
        # Unknown user type
        # -------------------------
        return Response({
            "status": 0,
            "message": "Unknown user type",
            "data": None
        }, status=200)


        
 # ========================================= optional  [GET ALL REGISTER USER WITH IT'S BUSINESS DATA TYPE API]  ========================================= #

class GetUserBusinessAPI(APIView):
    # permission_classes = [IsAuthenticated]
    @swagger_auto_schema(
        security=[{'Bearer': []}],
        operation_description="Get registered users filtered by user_type name (Rider, LeaseAgency, Owner), optionally by user_id",
        manual_parameters=[
            openapi.Parameter(
                "user_type",
                openapi.IN_QUERY,
                description="Name of the user type (Rider, LeaseAgency, Owner)",
                type=openapi.TYPE_STRING,
                required=True
            ),
            openapi.Parameter(
                "user_id",
                openapi.IN_QUERY,
                description="UUID of a specific user (optional)",
                type=openapi.TYPE_STRING,
                required=False
            )
        ],
        responses={
            1: openapi.Response(description="Users fetched successfully"),
            0: openapi.Response(description="No users found")
        }
    )
    def get(self, request):
        user_type_input = request.query_params.get("user_type")
        user_id_input = request.query_params.get("user_id")  # optional

        if not user_type_input:
            return Response({"status": 0, "message": "User type query param is required", "data": None}, status=200)

        try:
            user_type_obj = User_Type.objects.get(user_type_name__iexact=user_type_input)
        except User_Type.DoesNotExist:
            return Response({"status": 0, "message": "Invalid user type name", "data": None}, status=200)

        if user_id_input:
            try:
                user = User_Master.objects.get(id=user_id_input, user_type=user_type_obj)
                users = [user]
            except User_Master.DoesNotExist:
                return Response({"status": 0, "message": "Invalid user ID", "data": None}, status=200)
        else:
            users = User_Master.objects.filter(user_type=user_type_obj)
            if not users.exists():
                return Response({"status": 0, "message": "No user found for this type", "data": None}, status=200)

        user_list = []
        for user in users:
            business_response = None
            if user_type_obj.user_type_name == "LeaseAgency":
                try:
                    business_data = Lease_Agency_Master.objects.get(user_id=user)
                    business_response = {
                        "id": str(business_data.id),
                        "cac_number": business_data.cac_number,
                        "business_name": business_data.business_name,
                        "business_email": business_data.business_Email,
                        "business_number": business_data.business_number,
                        "business_type": business_data.business_type,
                        "phone_number": business_data.phone_number,
                        "year": business_data.year,
                        "state": business_data.state,
                        "company_name": business_data.company_name,
                        "full_name": business_data.full_name
                    }
                except Lease_Agency_Master.DoesNotExist:
                    business_response = None
            elif user_type_obj.user_type_name == "Owner":
                try:
                    business_data = Vehicle_Owner_Master.objects.get(user_id=user)
                    business_response = {
                        "id": str(business_data.id),
                        "cac_number": business_data.cac_number,
                        "business_name": business_data.business_name,
                        "business_email": business_data.business_Email,
                        "business_number": business_data.business_number,
                        "business_type": business_data.business_type,
                        "phone_number": business_data.phone_number,
                        "year": business_data.year,
                        "state": business_data.state,
                        "full_name": business_data.full_name
                    }
                except Vehicle_Owner_Master.DoesNotExist:
                    business_response = None

            user_list.append({
                "user_id": str(user.id),
                "first_name": user.first_name,
                "last_name": user.last_name,
                "email": user.email,
                "phone_number": user.phone_number,
                "user_type": user.user_type.user_type_name,
                "business_data": business_response
            })

        return Response({
            "status": 1,
            "message": "Users fetched successfully",
            "data": user_list
        }, status=200)


# =========================================   UPDATE ALL REGISTER USER TYPE API  ========================================= #


# class UpdateUserAPI(APIView):
#     @swagger_auto_schema(
#         operation_description="Update a registered user by user_id",
#         manual_parameters=[
#             openapi.Parameter(
#                 "user_id",
#                 openapi.IN_QUERY,
#                 description="UUID of the user to update",
#                 type=openapi.TYPE_STRING,
#                 required=True
#             )
#         ],
#         request_body=openapi.Schema(
#             type=openapi.TYPE_OBJECT,
#             properties={
#                 "first_name": openapi.Schema(type=openapi.TYPE_STRING),
#                 "last_name": openapi.Schema(type=openapi.TYPE_STRING),
#             }
#         ),
#         responses={
#             1: openapi.Response(description="User updated successfully"),
#             0: openapi.Response(description="User not found"),
#             0: openapi.Response(description="Invalid data")
#         }
#     )
#     def patch(self, request):
#         user_id_input = request.query_params.get("user_id")
#         if not user_id_input:
#             return Response({"status": 0, "message": "user_id query param is required", "data": None}, status=200)

#         try:
#             user = User_Master.objects.get(id=user_id_input)
#         except User_Master.DoesNotExist:
#             return Response({"status": 0, "message": "User not found", "data": None}, status=200)

#         data = request.data

#         try:
#             with transaction.atomic():
#                 # Update User_Master
#                 serializer = UserSerializers(user, data=data, partial=True)
#                 if not serializer.is_valid():
#                     return Response({"status": 0, "message": "Invalid data", "errors": serializer.errors,"data":None}, status=200)
#                 serializer.save()

#                 # Update Lease_Agency_Master
#                 lease_agencies = Lease_Agency_Master.objects.filter(user_id=user)
#                 for lease in lease_agencies:
#                     if 'first_name' in data:
#                         lease.full_name = f"{data['first_name']} {lease.full_name.split(' ')[-1]}"
#                     if 'last_name' in data:
#                         lease.full_name = f"{lease.full_name.split(' ')[0]} {data['last_name']}"
#                     lease.save()

#                 # Update Vehicle_Owner_Master
#                 owners = Vehicle_Owner_Master.objects.filter(user_id=user)
#                 for owner in owners:
#                     if 'first_name' in data:
#                         owner.full_name = f"{data['first_name']} {owner.full_name.split(' ')[-1]}"
#                     if 'last_name' in data:
#                         owner.full_name = f"{owner.full_name.split(' ')[0]} {data['last_name']}"
#                     owner.save()

#             return Response({"status": 1, "message": "User updated successfully in all tables", "data": serializer.data}, status=200)

#         except Exception as e:
#             return Response({"status": 0, "message": "Something went wrong while updating user", "error": str(e),"data":None}, status=200)
    
# =====================================   User UPDATE Part =================================== #

#-------------------------------------- Rider Update -----------------------------------#

# class UpdateRiderAPI(APIView):
#     permission_classes = [IsAuthenticated]
#     @swagger_auto_schema(
#         operation_summary="Update Rider Profile",
#         operation_description=(
#             "Updates only Rider profile information.\n\n"
#             "**Notes:**\n"
#             "- Rider has no business, contact, or driver information.\n"
#             "- Only personal profile fields are allowed (first_name, last_name, phone_number, email, etc.)"
#         ),
#         manual_parameters=[
#             openapi.Parameter(
#                 "user_id",
#                 openapi.IN_QUERY,
#                 description="UUID of the Rider to update",
#                 type=openapi.TYPE_STRING,
#                 required=True
#             ),
#         ],
#         request_body=openapi.Schema(
#             type=openapi.TYPE_OBJECT,
#             description="Fields you want to update",
#             properties={
#                 "first_name": openapi.Schema(type=openapi.TYPE_STRING, example="John"),
#                 "last_name": openapi.Schema(type=openapi.TYPE_STRING, example="Adamu"),
#                 "email": openapi.Schema(type=openapi.TYPE_STRING, example="johnadamu@gmail.com"),
#                 "phone_number": openapi.Schema(type=openapi.TYPE_STRING, example="08022223333"),
#                 "gender": openapi.Schema(type=openapi.TYPE_STRING, example="M"),
#                 "address": openapi.Schema(type=openapi.TYPE_STRING, example="Lekki Phase 1, Lagos"),
#             },
#         ),
#         responses={
#             200: openapi.Response(
#                 description="Formatted API response",
#                 examples={
#                     "application/json": {
#                         "success_response": {
#                             "status": 1,
#                             "message": "Rider updated successfully",
#                             "data": {
#                                 "user_id": "3de970d0-6ffc-4b80-90d6-98bb356eac35",
#                                 "first_name": "John",
#                                 "last_name": "Adamu",
#                                 "email": "johnadamu@gmail.com",
#                                 "phone_number": "08022223333"
#                             }
#                         },
#                         "error_response": {
#                             "status": 0,
#                             "message": "User not found",
#                             "data": None
#                         }
#                     }
#                 }
#             )
#         }
#     )
#     def patch(self, request):
#         user_id = request.query_params.get("user_id")
#         if not user_id:
#             return Response({"status": 0, "message": "user_id is required","data": None}, status=200)

#         try:
#             user = User_Master.objects.get(id=user_id)
#         except User_Master.DoesNotExist:
#             return Response({"status": 0, "message": "User not found","data": None}, status=200)

#         serializer = UserSerializers(user, data=request.data, partial=True)
#         if not serializer.is_valid():
#             return Response({"status": 0, "message": "Invalid data", "errors": serializer.errors},status=200)

#         serializer.save()
#         return Response({"status": 1, "message": "Rider updated successfully", "data": serializer.data},status=200)

class UpdateRiderAPI(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Update Rider Profile",
        operation_description="Updates only first_name and last_name for Rider.",
        manual_parameters=[
            openapi.Parameter(
                "user_id",
                openapi.IN_QUERY,
                description="UUID of the Rider",
                type=openapi.TYPE_STRING,
                required=True
            ),
        ],
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                "first_name": openapi.Schema(type=openapi.TYPE_STRING, example="John"),
                "last_name": openapi.Schema(type=openapi.TYPE_STRING, example="Adamu"),
            }
        ),
        responses={200: "Rider Updated Response"}
    )
    def patch(self, request):
        user_id = request.query_params.get("user_id")
        if not user_id:
            return Response({"status": 0, "message": "user_id is required", "data": None}, status=200)

        try:
            user = User_Master.objects.get(id=user_id)
        except User_Master.DoesNotExist:
            return Response({"status": 0, "message": "User not found", "data": None}, status=200)

        # Allow only firstname & lastname
        allowed_fields = {"first_name", "last_name"}
        update_data = {k: v for k, v in request.data.items() if k in allowed_fields}

        serializer = UserSerializers(user, data=update_data, partial=True)
        if not serializer.is_valid():
            return Response({"status": 0, "message": "Invalid data", "errors": serializer.errors}, status=200)

        serializer.save()
        return Response({"status": 1, "message": "Rider updated successfully", "data": serializer.data}, status=200)


#-------------------------------------- Agency Update -----------------------------------#

class UpdateLeaseAgencyAPI(APIView):
    permission_classes = [IsAuthenticated]
    @swagger_auto_schema(
        operation_summary="Update Lease Agency (User + Business + Contact info)",
        operation_description=(
            "Updates a Lease Agency user's profile.\n\n"
            "**Supports:**\n"
            "- Personal fields (first_name, last_name, phone_number, email, etc.)\n"
            "- Business information (business_name, business_email, business_number, business_type, address, state, year, etc.)\n"
            "- Contact info add/update/delete based on ID\n\n"
            "**Contact Rules:**\n"
            "- If contact contains `id` → update contact\n"
            "- If contact does NOT contain `id` → create new contact\n"
            "- If a contact exists in DB but NOT in request → delete"
        ),
        manual_parameters=[
            openapi.Parameter(
                "user_id",
                openapi.IN_QUERY,
                description="UUID of the Lease Agency user",
                type=openapi.TYPE_STRING,
                required=True
            )
        ],
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            description="Lease Agency update body",
            properties={
                # USER FIELDS
                "first_name": openapi.Schema(type=openapi.TYPE_STRING, example="John"),
                "last_name": openapi.Schema(type=openapi.TYPE_STRING, example="Adamu"),
                "email": openapi.Schema(type=openapi.TYPE_STRING, example="johnadamu@gmail.com"),
                "phone_number": openapi.Schema(type=openapi.TYPE_STRING, example="08022223333"),

                # BUSINESS FIELDS
                "business_data": openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "business_name": openapi.Schema(type=openapi.TYPE_STRING, example="Adamu Logistics Ltd"),
                        "business_email": openapi.Schema(type=openapi.TYPE_STRING, example="contact@adamu.com"),
                        "business_number": openapi.Schema(type=openapi.TYPE_STRING, example="08099998888"),
                        "business_type": openapi.Schema(type=openapi.TYPE_STRING, example="Transport"),
                        "phone_number": openapi.Schema(type=openapi.TYPE_STRING, example="08077770000"),
                        "address": openapi.Schema(type=openapi.TYPE_STRING, example="Plot 22 Lekki Phase 1, Lagos"),
                        "state": openapi.Schema(type=openapi.TYPE_STRING, example="Lagos"),
                        "year": openapi.Schema(type=openapi.TYPE_STRING, example="2015"),
                        "company_name": openapi.Schema(type=openapi.TYPE_STRING, example="Adamu Logistics"),
                        "cac_number": openapi.Schema(type=openapi.TYPE_STRING, example="CAC123456"),
                    },
                ),

                # CONTACT FIELDS
                "contact_info": openapi.Schema(
                    type=openapi.TYPE_ARRAY,
                    description="Add / Update / Remove Contact list",
                    items=openapi.Schema(
                        type=openapi.TYPE_OBJECT,
                        properties={
                            "id": openapi.Schema(
                                type=openapi.TYPE_STRING,
                                example="4353ce9f-587f-4419-99f2-8d36bc2c8cb7",
                                description="Required only when updating an existing contact"
                            ),
                            "owner_name": openapi.Schema(type=openapi.TYPE_STRING, example="Support Center"),
                            "email": openapi.Schema(type=openapi.TYPE_STRING, example="support@adamu.com"),
                            "phone_number": openapi.Schema(type=openapi.TYPE_STRING, example="08011110000"),
                        }
                    )
                ),
            }
        ),
        responses={
            200: openapi.Response(
                description="Formatted response",
                examples={
                    "application/json": {
                        "success_response": {
                            "status": 1,
                            "message": "Lease Agency updated successfully",
                            "data": {
                                "user_id": "4546d69d-b2e7-4084-96ad-ae6cc28e78ac",
                                "first_name": "John",
                                "last_name": "Adamu",
                                "email": "johnadamu@gmail.com",
                                "phone_number": "08022223333"
                            }
                        },
                        "error_response": {
                            "status": 0,
                            "message": "Lease Agency user not found",
                            "data": None
                        }
                    }
                }
            )
        }
    )
    def patch(self, request):
        user_id = request.query_params.get("user_id")
        if not user_id:
            return Response({"status": 0, "message": "user_id is required","data": None}, status=200)

        try:
            user = User_Master.objects.get(id=user_id, user_type__user_type_name="LeaseAgency")
        except User_Master.DoesNotExist:
            return Response({"status": 0, "message": "Lease Agency user not found","data": None}, status=200)

        data = request.data
        business_data = data.get("business_data")
        contact_info = data.get("contact_info")

        with transaction.atomic():

            # Update User fields
            serializer = UserSerializers(user, data=data, partial=True)
            if not serializer.is_valid():
                return Response({"status": 0, "message": "Invalid user data", "errors": serializer.errors}, status=200)
            serializer.save()

            # Update Business
            agency = Lease_Agency_Master.objects.filter(user_id=user).first()
            if agency and business_data:
                for field, value in business_data.items():
                    if hasattr(agency, field):
                        setattr(agency, field, value)
                agency.full_name = f"{user.first_name} {user.last_name}"
                agency.save()

            if contact_info:
                existing = {str(c.id): c for c in ContactInfo.objects.filter(user=user)}
                for info in contact_info:
                    cid = info.get("id")
                    if cid and cid in existing:
                        c = existing.pop(cid)
                        c.owner_name = info.get("owner_name", c.owner_name)
                        c.email = info.get("email", c.email)
                        c.phone_number = info.get("phone_number", c.phone_number)
                        c.save()
                    else:
                        ContactInfo.objects.create(
                            user=user,
                            owner_name=info.get("owner_name"),
                            email=info.get("email"),
                            phone_number=info.get("phone_number"),
                        )
                for remove in existing.values():
                    remove.delete()

        return Response({"status": 1, "message": "Lease Agency updated successfully","data": serializer.data},status=200)


#-------------------------------------- Owner Update -----------------------------------#

class UpdateVehicleOwnerAPI(APIView):
    permission_classes = [IsAuthenticated]
    @swagger_auto_schema(
        operation_summary="Update Vehicle Owner (User + Business + Drivers)",
        operation_description=(
            "Updates a Vehicle Owner's profile.\n\n"
            "**Supports:**\n"
            "- Update personal profile fields (first_name, last_name, email, phone_number)\n"
            "- Update business details (business_name, business_email, bank/account info, agency selection)\n"
            "- Update drivers with ID-based logic\n\n"
            "**Driver Logic:**\n"
            "- If a driver object contains `id` → update existing driver\n"
            "- If it does not contain `id` → create new driver\n"
            "- If a driver exists in DB but is NOT included → delete automatically"
        ),
        manual_parameters=[
            openapi.Parameter(
                "user_id",
                openapi.IN_QUERY,
                description="UUID of the Vehicle Owner to update",
                type=openapi.TYPE_STRING,
                required=True,
            ),
        ],
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                "first_name": openapi.Schema(type=openapi.TYPE_STRING, example="John"),
                "last_name": openapi.Schema(type=openapi.TYPE_STRING, example="Adamu"),
                "phone_number": openapi.Schema(type=openapi.TYPE_STRING, example="08022223333"),
                "email": openapi.Schema(type=openapi.TYPE_STRING, example="johnadamu@gmail.com"),
                "business_data": openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "business_name": openapi.Schema(type=openapi.TYPE_STRING, example="Adamu Motors Ltd"),
                        "business_email": openapi.Schema(type=openapi.TYPE_STRING, example="contact@adamu.com"),
                        "business_number": openapi.Schema(type=openapi.TYPE_STRING, example="08099998888"),
                        "business_type": openapi.Schema(type=openapi.TYPE_STRING, example="Transport"),
                        "phone_number": openapi.Schema(type=openapi.TYPE_STRING, example="08077770000"),
                        "address": openapi.Schema(type=openapi.TYPE_STRING, example="Lekki Phase 1, Lagos"),
                        "state": openapi.Schema(type=openapi.TYPE_STRING, example="Lagos"),
                        "year": openapi.Schema(type=openapi.TYPE_STRING, example="2020"),
                        "company_name": openapi.Schema(type=openapi.TYPE_STRING, example="Adamu Logistics"),

                        "name_of_bank": openapi.Schema(type=openapi.TYPE_STRING, example="Access Bank"),
                        "account_name": openapi.Schema(type=openapi.TYPE_STRING, example="Adamu Logistics Account"),
                        "account_number": openapi.Schema(type=openapi.TYPE_STRING, example="0112345678"),

                        "agency_id": openapi.Schema(type=openapi.TYPE_STRING, example="fd42ac5e-d2e0-4e4f-8651-93d8da4abf34"),
                    },
                ),

                "driver_info": openapi.Schema(
                    type=openapi.TYPE_ARRAY,
                    items=openapi.Schema(
                        type=openapi.TYPE_OBJECT,
                        properties={
                            "id": openapi.Schema(
                                type=openapi.TYPE_STRING,
                                example="759c008d-4ad8-4ba9-ac7b-11fa4b6e7e32",
                                description="Required only during update of an existing driver"
                            ),
                            "name": openapi.Schema(type=openapi.TYPE_STRING, example="Michael"),
                            "email": openapi.Schema(type=openapi.TYPE_STRING, example="michael@mail.com"),
                            "phone_number": openapi.Schema(type=openapi.TYPE_STRING, example="09022221111"),
                        }
                    )
                ),
            }
        ),
        responses={
            200: openapi.Response(
                description="Formatted response",
                examples={
                    "application/json": {
                        "success_response": {
                            "status": 1,
                            "message": "Vehicle Owner updated successfully",
                            "data": {
                                "user_id": "00ebf5c4-bab3-4b52-946b-3815ff5765d3",
                                "first_name": "John",
                                "last_name": "Adamu",
                                "email": "johnadamu@gmail.com",
                                "phone_number": "08022223333"
                            }
                        },
                        "error_response": {
                            "status": 0,
                            "message": "Vehicle Owner user not found",
                            "data": None
                        }
                    }
                },
            ),
        }
    )

    def patch(self, request):
        user_id = request.query_params.get("user_id")
        if not user_id:
            return Response({"status": 0, "message": "user_id is required", "data": None}, status=200)

        try:
            user = User_Master.objects.get(id=user_id, user_type__user_type_name="Owner")
        except User_Master.DoesNotExist:
            return Response({"status": 0, "message": "Vehicle Owner user not found", "data": None}, status=200)


        data = request.data
        business_data = data.get("business_data")
        driver_info = data.get("driver_info")

        if business_data:
            account_number = business_data.get("account_number")
            if account_number and not re.fullmatch(r"\d+", account_number):
                return Response({
                    "status": 0,
                    "message": "Invalid account number: must contain only digits",
                    "data": None
                }, status=200)
            
        with transaction.atomic():

            # Update User fields
            serializer = UserSerializers(user, data=data, partial=True)
            if not serializer.is_valid():
                return Response({"status": 0, "message": "Invalid user data", "errors": serializer.errors, "data": None}, status=200)

            serializer.save()

            # Update Business
            owner = Vehicle_Owner_Master.objects.filter(user_id=user).first()
            if owner and business_data:
                for field, value in business_data.items():
                    if hasattr(owner, field) and field != "agency_id":
                        setattr(owner, field, value)
                owner.full_name = f"{user.first_name} {user.last_name}"
                agency_id = business_data.get("agency_id")
                if agency_id:
                    owner.agency_id = agency_id
                owner.save()

            if driver_info:
                existing = {str(d.id): d for d in Vehicle_Owner_Driver.objects.filter(vehicle_owner=owner)}
                for d in driver_info:
                    did = d.get("id")
                    if did and did in existing:
                        drv = existing.pop(did)
                        drv.name = d.get("name", drv.name)
                        drv.email = d.get("email", drv.email)
                        drv.phone_number = d.get("phone_number", drv.phone_number)
                        drv.save()
                    else:
                        Vehicle_Owner_Driver.objects.create(
                            vehicle_owner=owner,
                            name=d.get("name"),
                            email=d.get("email"),
                            phone_number=d.get("phone_number"),
                        )
                for delete_item in existing.values():
                    delete_item.delete()

        return Response({"status": 1, "message": "Vehicle Owner updated successfully","data": serializer.data},status=200)
    
# ======================================   LOGIN API   ======================================= #
class LoginAPI(APIView):
    @swagger_auto_schema(
        operation_description="Login user and verify credentials",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['email', 'password', 'user_type'],
            properties={
                'email': openapi.Schema(type=openapi.TYPE_STRING, description='User email'),
                'password': openapi.Schema(type=openapi.TYPE_STRING, description='User password'),
                'user_type': openapi.Schema(type=openapi.TYPE_STRING, description='User type name (not ID)'),
            },
        ),
        responses={
            200: openapi.Response(description="Login response"),
        }
    )
    def post(self, request):
        email = request.data.get('email')
        password = request.data.get('password')
        user_type = request.data.get('user_type')

        if not email:
            return Response({'status': 0, 'message': 'Email is required', 'data': None}, status=200)
        
        if not password:
            return Response({'status': 0, 'message': 'Password is required', 'data': None}, status=200)
        
        if not user_type:
            return Response({'status': 0, 'message': 'User type is required', 'data': None}, status=200)

        try:
            user = User_Master.objects.get(email=email)
        except User_Master.DoesNotExist:
            return Response({'status': 0, 'message': 'This email is not registered. Please sign up.', 'data': None}, status=200)

        if not check_password(password, user.password):
            return Response({'status': 0, 'message': 'Invalid credentials', 'data': None}, status=200)

        # Resolve user_type by NAME (case-insensitive)
        try:
            user_type_obj = User_Type.objects.get(user_type_name__iexact=user_type)
        except User_Type.DoesNotExist:
            return Response({
                "status": 0,
                "message": f"Invalid user type name '{user_type}'",
                "data": None
            }, status=200)

        if user.user_type.id != user_type_obj.id:
            return Response({
                'status': 0,
                'message': f"Role mismatch. You are registered as {user.user_type.user_type_name}",
                'data': None
            }, status=200)

        refresh = RefreshToken.for_user(user)
        access_token = str(refresh.access_token)
        refresh_token = str(refresh)

        serializer = UserSerializers(user)
        user_data = serializer.data
        # Convert photo path to full URL (if exists)
        if user_data.get('photo'):
            user_data['photo'] = request.build_absolute_uri(user_data['photo'])
        return Response({
            'status': 1,
            'message': 'Login successful.',
            'access_token': access_token,
            'refresh_token': refresh_token,
            'data': {
                **user_data,
                'user_type': user.user_type.user_type_name  
            }
        }, status=status.HTTP_200_OK)


# ======================================   Forgot Password API   ======================================= #


class Forgot_passwordAPI(APIView):
    @swagger_auto_schema(
    operation_description="Send OTP for forgot password process",
    request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        required=['email'],
        properties={
            'email': openapi.Schema(type=openapi.TYPE_STRING, description='Registered email address'),
        },
    ),
    responses={
        1: openapi.Response(description="OTP sent to registered email"),
        0: openapi.Response(description="Email is required"),
        0: openapi.Response(description="Email not registered"),
        0: openapi.Response(description="Internal server error")
        }
    )
    def post(self, request):
        email = request.data.get("email")
        
        if not email:
            return Response({'status':0,'message':'Email are required','data':None},status=200)
        
        try:
            user = User_Master.objects.get(email = email)
            
            otp = str(random.randint(1000, 9999))
            created = User_OTP_Master.objects.update_or_create(
                user=user,
                defaults = {'otp': otp, 'created_at': timezone.now()} 
            )

            subject = "Your Forgot Password Verification OTP"
            html_message = render_to_string('myuser/otp.html', {'user': user, 'otp': otp})
            recipient_email = user.email 

            print(f"Sending OTP email to: {recipient_email}")

            email_message = EmailMultiAlternatives(
                subject,
                f"Your Forgot Password OTP is {otp}. It is valid for 2 minutes.",
                settings.EMAIL_HOST_USER,
                [recipient_email] 
            )
            email_message.attach_alternative(html_message, "text/html")
            email_message.send(fail_silently=False)

            return Response({
                'status': 1,
                'message': 'OTP sent to your email',
                'data':[{'otp expired in': '2 min'}]
            }, status=status.HTTP_200_OK)
        
        except User_Master.DoesNotExist:
            return Response({'status':0, 'message': 'Email not registered','data':None}, status=200)

        except Exception as e:
            return Response({'status': 1, 'message': 'Internal Server Error', 'data': str(e)},status=200)


# ======================================   Forgot OTO API   ======================================= #

class Forgot_Otp_API(APIView):
    @swagger_auto_schema(
        operation_description="Verify OTP for forgot password process",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['email', 'otp'],
            properties={
                'email': openapi.Schema(type=openapi.TYPE_STRING, description='Registered email address'),
                'otp': openapi.Schema(type=openapi.TYPE_STRING, description='Received OTP'),
            },
        ),
        responses={
            1: openapi.Response(description="OTP verified successfully"),
            0: openapi.Response(description="Invalid or missing OTP"),
            0: openapi.Response(description="Email not registered"),
            0: openapi.Response(description="OTP expired, please request new OTP")
        }
    )
    def post(self, request):
        email = request.data.get('email')
        otp = request.data.get('otp')

        if not email or not otp:
            return Response({'status':0, 'message': 'Email and OTP are required','data':None}, status=200)

       
        forgot_email_entry = User_OTP_Master.objects.filter(user__email=email).first()
        if not forgot_email_entry:
            return Response({'status':0, 'message': 'This Email is not Registered','data':None}, status=200)

      
        if forgot_email_entry.otp != otp:
            return Response({'status':0, 'message': 'Invalid OTP','data':None}, status=200)

        # Check if OTP expired
        otp_expiry_time = forgot_email_entry.created_at + timedelta(minutes=2)
        if timezone.now() > otp_expiry_time:
            return Response({'status': 0, 'message': 'OTP expired. Please request a new one.','data':None}, status=200)

        return Response({'status': 1, 'message': 'OTP verified. Proceed to reset password.','data':None}, status=200)


# ======================================   RESEND FORGOT OTP API   ======================================= #


class Resend_Forgot_Otp_API(APIView):
    @swagger_auto_schema(
    operation_description="Resend OTP for forgot password verification",
    request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        required=['email'],
        properties={
            'email': openapi.Schema(type=openapi.TYPE_STRING, description='Registered email address'),
        },
    ),
    responses={
        1: openapi.Response(description="OTP resent successfully"),
        0: openapi.Response(description="Email is required"),
        0: openapi.Response(description="Email not found or not registered"),
        0: openapi.Response(description="Internal server error")
    }
)
    def post(self, request):
        email = request.data.get("email")
        print("resend_email:--",email)

        if not email:
            return Response({'status':0, 'message': 'Email is required','data':None}, status=200)

        try:
            user = User_Master.objects.get(email=email)
            print("user in resend:--",user)
            otp_entry, created = User_OTP_Master.objects.get_or_create(user=user)

            current_time = timezone.now()
            otp_validity_period = timedelta(minutes=2)

            # store the first OTP time
            if not otp_entry.created_at:
                otp_entry.created_at = current_time
                otp_entry.save()

            # Check if the OTP is still valid
            otp_expiry_time = otp_entry.created_at + otp_validity_period
            if current_time <= otp_expiry_time:
                otp = otp_entry.otp  # Resend current OTP
            else:
                # Generate a new OTP and update the time limit
                otp = str(random.randint(1000, 9999))
                otp_entry.otp = otp
                otp_entry.created_at = current_time  # Update timestamp
                otp_entry.save()

            # Send OTP via email
            subject = "Your Resend OTP for forgot password Verification"
            html_message = render_to_string('myuser/otp.html', {'user': user, 'otp': otp})
            recipient_email = user.email    

            email_message = EmailMultiAlternatives(
                subject,
                f"Your Resend OTP is {otp}. It is valid for 3 minutes.",
                settings.EMAIL_HOST_USER,
                [recipient_email]
            )
            email_message.attach_alternative(html_message, "text/html")
            email_message.send(fail_silently=False)

            return Response({
                'status': 1,
                'message': "OTP sent to your email. It is valid for 3 minutes.",
                'data': {
                    'email': user.email,
                    'otp_valid_till': otp_expiry_time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            }, status=status.HTTP_200_OK)

        except (User_Master.DoesNotExist):
            return Response({"status": 0, "message": "Email not found. Please register first.",'data':None}, status=200)
        except Exception as e:
            return Response({'status': 1, 'message': 'Internal Server Error', 'error': str(e),"data":None}, status=200)



# ======================================   RESET PASSWORD API   ======================================= #



class Reset_Password_API(APIView):
    # RESET PASSWORD API
    @swagger_auto_schema(
    operation_description="Reset password after OTP verification",
    request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        required=['email', 'new_password'],
        properties={
            'email': openapi.Schema(type=openapi.TYPE_STRING, description='Registered email address'),
            'new_password': openapi.Schema(type=openapi.TYPE_STRING, description='New password'),
            'confirm_password': openapi.Schema(type=openapi.TYPE_STRING, description='confirm password')
        },
    ),
    responses={
        1: openapi.Response(description="Password reset successfully"),
        0: openapi.Response(description="Invalid or duplicate password"),
        0: openapi.Response(description="Email not registered"),
        0: openapi.Response(description="Internal server error")
    }
)
    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True) 

        email = serializer.validated_data["email"]
        new_password = serializer.validated_data["new_password"]

        try:
            user = User_Master.objects.get(email=email)

            # Check if new password matches old password
            if check_password(new_password, user.password):
                return Response({
                    'status':0,
                    'message': 'You cannot use your previous password. Try another password.',
                    'data': None
                }, status=200)

            # Hash and update the new password
            user.password = make_password(new_password)
            user.save()

            # Delete any existing forgot password OTP
            User_OTP_Master.objects.filter(user=user).delete()

            return Response({'status': 1, 'message': 'Password reset successfully. You can now log in.', 'data': None}, status=status.HTTP_200_OK)

        except User_Master.DoesNotExist:
            return Response({'status':0, 'message': 'Email not registered', 'data': None }, status=200)

        except Exception as e:
            return Response({'status': 1, 'message': 'Internal Server Error', 'data': str(e)}, status=200)
            

# ======================================   Get Agency API   ======================================= #



class GetLeaseAgencyAPI(APIView):
    @swagger_auto_schema(
        operation_description="Get all lease agencies or a specific agency by ID",
        manual_parameters=[
            openapi.Parameter(
                'agency_id',
                openapi.IN_QUERY,
                description="UUID of the Lease Agency (optional)",
                type=openapi.TYPE_STRING,
                required=False
            ),
        ],
        responses={
            200: openapi.Response(
                description="Successful Response",
                examples={
                    "application/json": {
                        "status": 1,
                        "message": "All agencies fetched successfully",
                        "data": [
                            {
                                "id": "6bb82c41-e15e-4308-b99d-e9640818eca9",
                                "business_name": "Adamu Logistics Ltd.",
                                "company_name": "Adamu Logistics",
                                "business_Email": "info@adamulogistics.com",
                                "phone_number": "08099999999",
                                "state": "Lagos",
                                "full_name": "John Adamu"
                            },
                            {
                                "id": "f2d51b4a-2e1a-4a10-9f72-123456789abc",
                                "business_name": "Tara Transport Ltd.",
                                "company_name": "Tara Transport",
                                "business_Email": "contact@taratransport.com",
                                "phone_number": "08088888888",
                                "state": "Abuja",
                                "full_name": "Tara Smith"
                            }
                        ]
                    }
                }
            ),
            400: openapi.Response(
                description="Agency not found",
                examples={
                    "application/json": {
                        "status": 0,
                        "message": "Agency not found",
                        "data": None
                    }
                }
            ),
            500: openapi.Response(
                description="Server Error",
                examples={
                    "application/json": {
                        "status": 0,
                        "message": "Error fetching agencies: Internal Server Error",
                        "data": None
                    }
                }
            )
        }
    )
    def get(self, request):
        try:
            agency_id = request.GET.get('agency_id')

            if agency_id:
                # fetch single agency
                agency = Lease_Agency_Master.objects.filter(id=agency_id).first()
                if not agency:
                    return Response(
                        {"status": 0, "message": "Agency not found", "data": None},
                        status=200
                    )
                serializer = LeaseAgencySerializer(agency)
                return Response(
                    {"status": 1, "message": "Agency details fetched successfully", "data": serializer.data},
                    status=status.HTTP_200_OK
                )

            # fetch all agencies
            agencies = Lease_Agency_Master.objects.all()
            serializer = GetLeaseAgencySerializer(agencies, many=True, context={"request": request})
            return Response(
                {"status": 1, "message": "All agencies fetched successfully", "data": serializer.data},
                status=status.HTTP_200_OK
            )

        except Exception as e:
            return Response(
                {"status": 0, "message": f"Error fetching agencies: {str(e)}", "data": None},
                status=200
            )

# ======================================   CREATE VEHICLE API   ======================================= #

class VehicleCreateAPI(APIView):
    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        security=[{'Bearer': []}],
        operation_summary="Create a Vehicle and Link to a Lease Agency",
        operation_description=(
            "Creates a vehicle under a vehicle owner and links it to a Lease Agency. "
            "Uploads 1–5 images (5MB max each). Includes all document & vehicle data."
        ),
        consumes=["multipart/form-data"],
        manual_parameters=[
            openapi.Parameter("vehicle_owner_id", openapi.IN_FORM, type=openapi.TYPE_INTEGER, required=True),
            openapi.Parameter("lease_agency_id", openapi.IN_FORM, type=openapi.TYPE_INTEGER, required=True),
            openapi.Parameter("registered_owner", openapi.IN_FORM, type=openapi.TYPE_STRING),
            openapi.Parameter("plate_number", openapi.IN_FORM, type=openapi.TYPE_STRING),
            openapi.Parameter("vehicle_make", openapi.IN_FORM, type=openapi.TYPE_STRING),
            openapi.Parameter("vehicle_model", openapi.IN_FORM, type=openapi.TYPE_STRING),
            openapi.Parameter("body_type", openapi.IN_FORM, type=openapi.TYPE_STRING),
            openapi.Parameter("mfg_year", openapi.IN_FORM, type=openapi.TYPE_INTEGER),
            openapi.Parameter("vehicle_identify_number", openapi.IN_FORM, type=openapi.TYPE_STRING),
            openapi.Parameter("license_renewed_date", openapi.IN_FORM, type=openapi.TYPE_STRING, description="YYYY-MM-DD"),
            openapi.Parameter("license_expiry_date", openapi.IN_FORM, type=openapi.TYPE_STRING, description="YYYY-MM-DD"),
            openapi.Parameter("insurance_renewed_date", openapi.IN_FORM, type=openapi.TYPE_STRING, description="YYYY-MM-DD"),
            openapi.Parameter("insurance_expiry_date", openapi.IN_FORM, type=openapi.TYPE_STRING, description="YYYY-MM-DD"),
            openapi.Parameter("road_worthiness_cert_date", openapi.IN_FORM, type=openapi.TYPE_STRING, description="YYYY-MM-DD"),
            openapi.Parameter("road_worthiness_expiry_date", openapi.IN_FORM, type=openapi.TYPE_STRING, description="YYYY-MM-DD"),
            openapi.Parameter("engine_spec", openapi.IN_FORM, type=openapi.TYPE_STRING),
            openapi.Parameter("other_spec", openapi.IN_FORM, type=openapi.TYPE_STRING),
            openapi.Parameter("primary_location", openapi.IN_FORM, type=openapi.TYPE_STRING),
            openapi.Parameter("lease_price_per_day", openapi.IN_FORM, type=openapi.TYPE_NUMBER),
            openapi.Parameter("active", openapi.IN_FORM, type=openapi.TYPE_BOOLEAN),
            openapi.Parameter("passenger_count", openapi.IN_FORM, type=openapi.TYPE_INTEGER),
            openapi.Parameter(
                "images",
                openapi.IN_FORM,
                type=openapi.TYPE_FILE,
                required=True,
                description="Upload 1–5 images (5MB max each)",
                multiple=True
            ),
        ]
    )
    def post(self, request):
        try:
            # -------------------------------
            # Required fields
            # -------------------------------
            vehicle_owner_id = request.data.get("vehicle_owner_id")
            lease_agency_id = request.data.get("lease_agency_id")

            if not vehicle_owner_id or not lease_agency_id:
                return Response({
                    "status": 0,
                    "message": "vehicle_owner_id and lease_agency_id are required.",
                    "data": None
                }, status=200)

            # Validate Owner
            vehicle_owner = Vehicle_Owner_Master.objects.filter(id=vehicle_owner_id).first()
            if not vehicle_owner:
                return Response({
                    "status": 0,
                    "message": "Invalid vehicle_owner_id.",
                    "data": None
                }, status=200)

            # Validate Lease Agency
            lease_agency = Lease_Agency_Master.objects.filter(id=lease_agency_id).first()
            if not lease_agency:
                return Response({
                    "status": 0,
                    "message": "Invalid lease_agency_id.",
                    "data": None
                }, status=200)

            images = request.FILES.getlist("images")

            if not images:
                return Response({
                    **{"status": 0, "message": "At least one image is required.", "data": None}
                }, status=200)

            if len(images) > 5:
                return Response({
                    "status": 0,
                    "message": "Maximum 5 images allowed.",
                    "data": None
                }, status=200)

            for img in images:
                if img.size > 5 * 1024 * 1024:  # 5MB
                    return Response({
                        "status": 0,
                        "message": f"Image '{img.name}' exceeds 5MB limit.",
                        "data": None
                    }, status=200)
            # -------------------------------
            # Parse Dates
            # -------------------------------
            def parse_date(value):
                if not value:
                    return None
                try:
                    return datetime.strptime(value, "%Y-%m-%d").date()
                except:
                    return None

            # -------------------------------
            # Create Vehicle
            # -------------------------------
            vehicle = Vehicle_Master.objects.create(
                vehicle_owner=vehicle_owner,
                registered_owner=request.data.get("registered_owner"),
                plate_number=request.data.get("plate_number"),
                vehicle_make=request.data.get("vehicle_make"),
                vehicle_model=request.data.get("vehicle_model"),
                body_type=request.data.get("body_type"),
                mfg_year=request.data.get("mfg_year"),
                vehicle_identify_number=request.data.get("vehicle_identify_number"),
                license_renewed_date=parse_date(request.data.get("license_renewed_date")),
                license_expiry_date=parse_date(request.data.get("license_expiry_date")),
                insurance_renewed_date=parse_date(request.data.get("insurance_renewed_date")),
                insurance_expiry_date=parse_date(request.data.get("insurance_expiry_date")),
                road_worthiness_cert_date=parse_date(request.data.get("road_worthiness_cert_date")),
                road_worthiness_expiry_date=parse_date(request.data.get("road_worthiness_expiry_date")),
                engine_spec=request.data.get("engine_spec"),
                other_spec=request.data.get("other_spec"),
                primary_location=request.data.get("primary_location"),
                lease_price_per_day=request.data.get("lease_price_per_day"),
                active=request.data.get("active", "True"),
                passenger_count = request.data.get("passenger_count"),
            )

            # -------------------------------
            # Link to Lease Agency
            # -------------------------------
            Vehicle_Agency.objects.create(
                vehicle_master=vehicle,
                lease_agency=lease_agency,
                status="Active"
            )

            # -------------------------------
            # Upload up to 5 images
            # -------------------------------

            uploaded_images = []
            for img in images:

                img_obj = Vehicle_Image.objects.create(vehicle_master=vehicle, image=img)
                uploaded_images.append(request.build_absolute_uri(img_obj.image.url))

            # -------------------------------
            # SUCCESS RESPONSE
            # -------------------------------
            return Response({
                "status": 1,
                "message": "Vehicle created and linked to agency successfully.",
                "data": {
                    "vehicle_id": str(vehicle.id),
                    "plate_number": vehicle.plate_number,
                    "vehicle_status": vehicle.vehicle_status.vehicle_status_name,
                    "lease_agency_name": lease_agency.business_name,
                    "images": uploaded_images,
                    "mfg_year": vehicle.mfg_year,
                    "created_at":vehicle.created_at
                }
            }, status=201)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Error creating vehicle.",
                "error": str(e),
                "data": None
            }, status=200)


# class VehicleCreateAPI(APIView):
#     parser_classes = [MultiPartParser, FormParser]
#     # permission_classes = [IsAuthenticated]

#     @swagger_auto_schema(
#         security=[{'Bearer': []}],
#         operation_description="Register a vehicle under a lease agency with all document details (license, insurance, road worthiness) and upload up to 5 images (max 5MB each).",
#         manual_parameters=[
#             openapi.Parameter('vehicle_owner_id', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="UUID of Business Vehicle Owner id"),
#             openapi.Parameter('lease_agency_id', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="UUID of Business Lease Agency"),
#             openapi.Parameter('registration_number', openapi.IN_FORM, type=openapi.TYPE_STRING, description="Vehicle registration number"),
#             openapi.Parameter('plate_number', openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Vehicle plate number"),
#             openapi.Parameter('vehicle_make', openapi.IN_FORM, type=openapi.TYPE_STRING, description="Vehicle make"),
#             openapi.Parameter('vehicle_model', openapi.IN_FORM, type=openapi.TYPE_STRING, description="Vehicle model"),
#             openapi.Parameter('body_type', openapi.IN_FORM, type=openapi.TYPE_STRING, description="Body type"),
#             openapi.Parameter('mfg_year', openapi.IN_FORM, type=openapi.TYPE_INTEGER, description="Manufacturing year"),
#             openapi.Parameter('vehicle_identify_number', openapi.IN_FORM, type=openapi.TYPE_STRING, description="VIN or chassis number"),
#             openapi.Parameter('license_renewed_date', openapi.IN_FORM, type=openapi.TYPE_STRING, description="YYYY-MM-DDTHH:MM:SS"),
#             openapi.Parameter('license_expiry_date', openapi.IN_FORM, type=openapi.TYPE_STRING, description="YYYY-MM-DD"),
#             openapi.Parameter('insurance_renewed_date', openapi.IN_FORM, type=openapi.TYPE_STRING, description="YYYY-MM-DDTHH:MM:SS"),
#             openapi.Parameter('insurance_expiry_date', openapi.IN_FORM, type=openapi.TYPE_STRING, description="YYYY-MM-DD"),
#             openapi.Parameter('road_worthiness_cert_date', openapi.IN_FORM, type=openapi.TYPE_STRING, description="YYYY-MM-DD"),
#             openapi.Parameter('road_worthiness_expiry_date', openapi.IN_FORM, type=openapi.TYPE_STRING, description="YYYY-MM-DD"),
#             openapi.Parameter('engine_spec', openapi.IN_FORM, type=openapi.TYPE_STRING, description="Engine specification"),
#             openapi.Parameter('other_spec', openapi.IN_FORM, type=openapi.TYPE_STRING, description="Other specifications"),
#             openapi.Parameter('primary_location', openapi.IN_FORM, type=openapi.TYPE_STRING, description="Primary vehicle location"),
#             openapi.Parameter('lease_price_per_day', openapi.IN_FORM, type=openapi.TYPE_NUMBER, description="Lease price per day"),
#             openapi.Parameter('status', openapi.IN_FORM, type=openapi.TYPE_STRING, enum=["active", "deactive", "live", "offline", "attenions", "maintenance"]),
#             openapi.Parameter(
#                 'images', openapi.IN_FORM, type=openapi.TYPE_ARRAY, items=openapi.Items(type=openapi.TYPE_FILE),
#                 description="Upload 1–5 images (max 5MB each)", required=True
#             ),
#         ],
#         consumes=["multipart/form-data"]
#     )
#     def post(self, request):
#         try:
#             vehicle_owner_id = request.data.get("vehicle_owner_id")
#             lease_agency_id = request.data.get("lease_agency_id")

#             # Validate linked entities
#             vehicle_owner = Vehicle_Owner_Master.objects.filter(id=vehicle_owner_id).first()
#             lease_agency = Lease_Agency_Master.objects.filter(id=lease_agency_id).first()

#             if not vehicle_owner:
#                 return Response({"status": 0, "message": "Invalid vehicle_owner_id","data":None}, status=200)
#             if not lease_agency:
#                 return Response({"status": 0, "message": "Invalid lease_agency_id","data":None}, status=200)

#             # Convert date fields safely
#             def parse_date(value, with_time=False):
#                 if not value:
#                     return None
#                 try:
#                     return datetime.fromisoformat(value) if with_time else datetime.strptime(value, "%Y-%m-%d").date()
#                 except Exception:
#                     return None

#             # Create vehicle
#             vehicle = Vehicle_Master.objects.create(
#                 vehicle_owner=vehicle_owner,
#                 registration_number=request.data.get("registration_number"),
#                 plate_number=request.data.get("plate_number"),
#                 vehicle_make=request.data.get("vehicle_make"),
#                 vehicle_model=request.data.get("vehicle_model"),
#                 body_type=request.data.get("body_type"),
#                 mfg_year=request.data.get("mfg_year"),
#                 vehicle_identify_number=request.data.get("vehicle_identify_number"),
#                 license_renewed_date=parse_date(request.data.get("license_renewed_date"), with_time=True),
#                 license_expiry_date=parse_date(request.data.get("license_expiry_date")),
#                 insurance_renewed_date=parse_date(request.data.get("insurance_renewed_date"), with_time=True),
#                 insurance_expiry_date=parse_date(request.data.get("insurance_expiry_date")),
#                 road_worthiness_cert_date=parse_date(request.data.get("road_worthiness_cert_date")),
#                 road_worthiness_expiry_date=parse_date(request.data.get("road_worthiness_expiry_date")),
#                 engine_spec=request.data.get("engine_spec"),
#                 other_spec=request.data.get("other_spec"),
#                 primary_location=request.data.get("primary_location"),
#                 lease_price_per_day=request.data.get("lease_price_per_day"),
#                 status=request.data.get("status", "active"),
#             )

#             # Link to Lease Agency
#             vehicle_agency = Vehicle_Agency.objects.create(
#                 vehicle_master=vehicle,
#                 lease_agency=lease_agency,
#                 status=vehicle.status
#             )

#             # Handle images (max 5, each <= 5MB)
#             images = request.FILES.getlist("images")
#             if not images:
#                 return Response({"status": 0, "message": "At least one image is required.","data":None}, status=200)
#             if len(images) > 5:
#                 return Response({"status": 0, "message": "Maximum 5 images allowed.","data":None}, status=200)

#             uploaded_images = []
#             for img in images:
#                 if isinstance(img, UploadedFile) and img.size > 1024 * 1024 * 5:
#                     return Response({
#                         "status": 0,
#                         "message": f"Image {img.name} exceeds 5MB size limit.",
#                         "data":None
#                     }, status=200)
#                 vehicle_image = Vehicle_Image.objects.create(vehicle_master=vehicle, image=img)
#                 uploaded_images.append(request.build_absolute_uri(vehicle_image.image.url))

#             return Response({
#                 "status": 1,
#                 "message": "Vehicle created successfully.",
#                 "data": {
#                     "vehicle_id": str(vehicle.id),
#                     "plate_number": vehicle.plate_number,
#                     "status": vehicle.status,
#                     "lease_agency": lease_agency.business_name,
#                     "insurance_expiry_date": vehicle.insurance_expiry_date,
#                     "license_expiry_date": vehicle.license_expiry_date,
#                     "images": uploaded_images
#                 }
#             }, status=201)

#         except Exception as e:
#             return Response({
#                 "status": 0,
#                 "message": "Error creating vehicle",
#                 "error": str(e),
#                 "data":None
#             }, status=200)


# ======================================   GET ONLY VEHICLE API   ======================================= #

class GetVehicleListAPI(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        # security=[{'Bearer': []}],
        operation_summary="Get Vehicle(s) Details",
        operation_description=(
            "Fetch all vehicles or a single vehicle by ID (via query param). "
            "Includes details about owner, lease agency, and uploaded images."
        ),
        manual_parameters=[
            openapi.Parameter(
                'id',
                openapi.IN_QUERY,
                description="Vehicle ID (UUID) — Optional. If provided, fetches a single vehicle.",
                type=openapi.TYPE_STRING
            ),
        ],
        responses={
            1: openapi.Response(description="Vehicle(s) fetched successfully"),
            0: openapi.Response(description="No vehicle found"),
            0: openapi.Response(description="Server error"),
        }
    )
    def get(self, request):
        try:
            user = request.user  # Logged-in user

            # Get the vehicle owner record for this user
            vehicle_owner = Vehicle_Owner_Master.objects.filter(user_id=user).first()
            if not vehicle_owner:
                return Response({
                    "status": 0,
                    "message": "You are not registered as a vehicle owner",
                    "data": None
                },status=200)

            vehicle_id = request.query_params.get("id")

            # Filter vehicles for this owner that have at least one Vehicle_Agency
            vehicles = Vehicle_Master.objects.filter(
                vehicle_owner=vehicle_owner,
                vehicle_agencies__isnull=False
            ).distinct()


            if vehicle_id:
                vehicles = vehicles.filter(id=vehicle_id)

            if not vehicles.exists():
                return Response({
                    "status": 0,
                    "message": "No vehicles found",
                    "data": None,
                },status=200)

            # Build response
            data = []
            for vehicle in vehicles:
                # Get all active lease agencies linked to this vehicle
                agencies = vehicle.vehicle_agencies.filter(status="Active")
                agency_list = [
                    {
                        "id": str(va.lease_agency.id),
                        "name": va.lease_agency.business_name,
                        "status": va.status
                    } for va in agencies
                ]

                # Get images
                images = [request.build_absolute_uri(img.image.url) for img in vehicle.images.all()]

                data.append({
                    "id": str(vehicle.id),
                    "plate_number": vehicle.plate_number,
                    "vehicle_make": vehicle.vehicle_make,
                    "vehicle_model": vehicle.vehicle_model,
                    "vehicle_status": vehicle.vehicle_status.vehicle_status_name,
                    "lease_agencies": agency_list,
                    "images": images,
                    "mfg_year": vehicle.mfg_year,
                    "created_at":vehicle.created_at,
                    "passenger_count": vehicle.passenger_count,
                })

            return Response({
                "status": 1,
                "message": "Vehicle(s) fetched successfully",
                "data": data
            },status=200)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Server error",
                "error": str(e),
                "data": None
            }, status=200)

    # def get(self, request):
    #     try:
    #         vehicle_id = request.query_params.get("id")

    #         # Filter by ID if provided, else fetch all
    #         if vehicle_id:
    #             vehicles = Vehicle_Master.objects.filter(id=vehicle_id)
    #         else:
    #             vehicles = Vehicle_Master.objects.all().order_by('-created_at')

    #         if not vehicles.exists():
    #             return Response({
    #                 "status": 0,
    #                 "message": "No vehicle found",
    #                 "data": None
    #             }, status=200)

    #         # Helper function to format each vehicle record
    #         def format_vehicle(vehicle):
    #             # Owner Details
    #             owner = vehicle.vehicle_owner
    #             owner_data = {
    #                 "id": str(owner.id),
    #                 "owner_name": getattr(owner, "full_name", "") or getattr(owner, "business_name", ""),
    #                 "email": getattr(owner, "business_Email", ""),
    #             }

    #             # Lease Agency Details
    #             try:
    #                 vehicle_agency = Vehicle_Agency.objects.get(vehicle_master=vehicle)
    #                 lease_agency = vehicle_agency.lease_agency
    #                 agency_data = {
    #                     "id": str(lease_agency.id),
    #                     "business_name": lease_agency.business_name,
    #                     "business_email": lease_agency.business_Email,
    #                     "status": vehicle_agency.status
    #                 }
    #             except Vehicle_Agency.DoesNotExist:
    #                 agency_data = {}

    #             # Vehicle Images
    #             images = Vehicle_Image.objects.filter(vehicle_master=vehicle)
    #             image_urls = [request.build_absolute_uri(img.image.url) for img in images]

    #             return {
    #                 "id": str(vehicle.id),
    #                 "plate_number": vehicle.plate_number,
    #                 "vehicle_make": vehicle.vehicle_make,
    #                 "vehicle_model": vehicle.vehicle_model,
    #                 "body_type": vehicle.body_type,
    #                 "mfg_year": vehicle.mfg_year,
    #                 "lease_price_per_day": vehicle.lease_price_per_day,
    #                 "status": vehicle.status,
    #                 "primary_location": vehicle.primary_location,
    #                 "owner": owner_data,
    #                 "agency": agency_data,
    #                 "images": image_urls,
    #                 "created_at": vehicle.created_at,
    #             }

    #         # If single ID provided → return one record
    #         if vehicle_id:
    #             vehicle = vehicles.first()
    #             return Response({
    #                 "status": 1,
    #                 "message": "Vehicle fetched successfully",
    #                 "data": format_vehicle(vehicle)
    #             }, status=200)

    #         # Otherwise return all vehicles
    #         vehicle_list = [format_vehicle(v) for v in vehicles]
    #         return Response({
    #             "status": 1,
    #             "message": "Vehicle list fetched successfully",
    #             "data": vehicle_list
    #         }, status=200)

    #     except Exception as e:
    #         return Response({
    #             "status": 0,
    #             "message": "Something went wrong while fetching vehicles",
    #             "error": str(e),
    #             "data": None
    #         }, status=200)


# ======================================   GET LEASE VEHICLE API   ======================================= #
#  GET LEASE-VEHICLE'S WHICH STATUS IS [ACTIVE] AND ALSO ADD IN [NEW_ORDER] NOT BOOKED 


class GetLeaseVehicleAPI(APIView):
    permission_classes = [IsAuthenticated]
    @swagger_auto_schema(
        security=[{'Bearer': []}],
        operation_description=(
            "Get all active available vehicles or filter by lease agency, vehicle ID, or plate number. "
            "Excludes vehicles with active bookings (in_review, confirm). "
            "Includes order_status if the vehicle is currently booked."
        ),
        manual_parameters=[
            openapi.Parameter('agency_id', openapi.IN_QUERY, description="Lease Agency ID", type=openapi.TYPE_STRING),
            # openapi.Parameter('vehicle_id', openapi.IN_QUERY, description="Vehicle ID", type=openapi.TYPE_STRING),
            # openapi.Parameter('plate_number', openapi.IN_QUERY, description="Vehicle plate number (optional)", type=openapi.TYPE_STRING),
        ],
        responses={
            1: openapi.Response(description="Vehicle(s) fetched successfully"),
            0: openapi.Response(description="No vehicle found"),
            0: openapi.Response(description="Internal Server Error"),
        }
    )
    def get(self, request, id=None):
        try:
            agency_id = request.query_params.get("agency_id")
          
            # ---  Exclude vehicles with active bookings ---
            active_bookings = LeaseOrderMaster.objects.filter(
                order_status__order_status_name__in=["in_review", "confirm"],
                end_date__gte=datetime.now()
            ).values_list("vehicle_id", flat=True)

            # ---  Start with active vehicles only ---
            vehicle_filter = Q(status__iexact="Active")

            # ---  Apply filters based on parameters ---
            if agency_id:
                # First get vehicle IDs linked to this agency
                vehicle_ids = Vehicle_Agency.objects.filter(
                    lease_agency_id=agency_id, status__iexact="Active"
                ).values_list("vehicle_master_id", flat=True)

                vehicles = Vehicle_Master.objects.filter(vehicle_filter, id__in=vehicle_ids).exclude(id__in=active_bookings)

            # elif vehicle_id:
            #     vehicles = Vehicle_Master.objects.filter(vehicle_filter, id=vehicle_id).exclude(id__in=active_bookings)

            # elif plate_number:
            #     vehicles = Vehicle_Master.objects.filter(vehicle_filter, plate_number__iexact=plate_number).exclude(id__in=active_bookings)

            else:
                vehicles = Vehicle_Master.objects.filter(vehicle_filter).exclude(id__in=active_bookings)

            if not vehicles.exists():
                return Response({"status": 0, "message": "No available active vehicles found","data":None}, status=200)

            # ---  Helper: Format Vehicle Data ---
            def format_vehicle(vehicle):
                owner = vehicle.vehicle_owner
                owner_data = {
                    "id": str(owner.id),
                    "owner_name": getattr(owner, "full_name", "") or getattr(owner, "business_name", ""),
                    "email": getattr(owner, "business_Email", ""),
                }

                # Get Agency Data (if available)
                vehicle_agency = Vehicle_Agency.objects.filter(vehicle_master=vehicle).select_related("lease_agency").first()
                if vehicle_agency:
                    lease_agency = vehicle_agency.lease_agency
                    agency_data = {
                        "id": str(lease_agency.id),
                        "agency_name": lease_agency.business_name,
                        "business_email": lease_agency.business_Email,
                        "status": vehicle_agency.status,
                    }
                else:
                    agency_data = {}

                # Get Vehicle Images
                images = Vehicle_Image.objects.filter(vehicle_master=vehicle)
                image_urls = [request.build_absolute_uri(img.image.url) for img in images]

                # Get Latest Booking (if any)
                current_order = (
                    LeaseOrderMaster.objects.filter(vehicle=vehicle)
                    .select_related("order_status")
                    .order_by('-created_at')
                    .first()
                )
                order_status_data = (
                    {
                        "id": str(current_order.order_status.id),
                        "status_name": current_order.order_status.order_status_name,
                        "start_date": current_order.start_date,
                        "end_date": current_order.end_date,
                    }
                    if current_order else None
                )

                return {
                    "id": str(vehicle.id),
                    "plate_number": vehicle.plate_number,
                    "vehicle_make": vehicle.vehicle_make,
                    "vehicle_model": vehicle.vehicle_model,
                    "body_type": vehicle.body_type,
                    "mfg_year": vehicle.mfg_year,
                    "lease_price_per_day": vehicle.lease_price_per_day,
                    "vehicle_status": vehicle.vehicle_status.vehicle_status_name,
                    "primary_location": vehicle.primary_location,
                    "owner": owner_data,
                    "agency": agency_data,
                    "order_status": order_status_data,
                    "images": image_urls,
                    "passenger_count": vehicle.passenger_count,
                    "created_at": vehicle.created_at,
                }

            data = [format_vehicle(v) for v in vehicles]

            return Response({
                "status": 1,
                "message": "Available active vehicles fetched successfully",
                "count": len(data),
                "data": data
            }, status=200)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong while fetching vehicles",
                "error": str(e)
            }, status=200)

# ======================================  GET LEASE VEHICLES OPEN API ================================= #

class GetLeaseVehicleOpenAPI(APIView):
    @swagger_auto_schema(
        security=[{'Bearer': []}],
        operation_description=(
            "Get all active available vehicles or filter by lease agency, vehicle ID, or plate number. "
            "Excludes vehicles with active bookings (in_review, confirm). "
            "Includes order_status if the vehicle is currently booked."
        ),
        manual_parameters=[
            openapi.Parameter('agency_id', openapi.IN_QUERY, description="Lease Agency ID", type=openapi.TYPE_STRING),
            # openapi.Parameter('vehicle_id', openapi.IN_QUERY, description="Vehicle ID", type=openapi.TYPE_STRING),
            # openapi.Parameter('plate_number', openapi.IN_QUERY, description="Vehicle plate number (optional)", type=openapi.TYPE_STRING),
        ],
        responses={
            1: openapi.Response(description="Vehicle(s) fetched successfully"),
            0: openapi.Response(description="No vehicle found"),
            0: openapi.Response(description="Internal Server Error"),
        }
    )
    def get(self, request, id=None):
        try:
            agency_id = request.query_params.get("agency_id")
          
            # ---  Exclude vehicles with active bookings ---
            active_bookings = LeaseOrderMaster.objects.filter(
                order_status__order_status_name__in=["in_review", "confirm"],
                end_date__gte=datetime.now()
            ).values_list("vehicle_id", flat=True)

            # ---  Start with active vehicles only ---

            # ---  Apply filters based on parameters ---
            if agency_id:
                # First get vehicle IDs linked to this agency
                vehicle_ids = Vehicle_Agency.objects.filter(
                    lease_agency_id=agency_id, status__iexact="Active"
                ).values_list("vehicle_master_id", flat=True)

                vehicles = Vehicle_Master.objects.filter( id__in=vehicle_ids).exclude(id__in=active_bookings)

            # elif vehicle_id:
            #     vehicles = Vehicle_Master.objects.filter(vehicle_filter, id=vehicle_id).exclude(id__in=active_bookings)

            # elif plate_number:
            #     vehicles = Vehicle_Master.objects.filter(vehicle_filter, plate_number__iexact=plate_number).exclude(id__in=active_bookings)

            else:
                vehicles = Vehicle_Master.objects.filter().exclude(id__in=active_bookings)

            if not vehicles.exists():
                return Response({"status": 0, "message": "No available active vehicles found","data":None}, status=200)

            # ---  Helper: Format Vehicle Data ---
            def format_vehicle(vehicle):
                owner = vehicle.vehicle_owner
                owner_data = {
                    "id": str(owner.id),
                    "owner_name": getattr(owner, "full_name", "") or getattr(owner, "business_name", ""),
                    "email": getattr(owner, "business_Email", ""),
                }

                # Get Agency Data (if available)
                vehicle_agency = Vehicle_Agency.objects.filter(vehicle_master=vehicle).select_related("lease_agency").first()
                if vehicle_agency:
                    lease_agency = vehicle_agency.lease_agency
                    agency_data = {
                        "id": str(lease_agency.id),
                        "agency_name": lease_agency.business_name,
                        "business_email": lease_agency.business_Email,
                        "status": vehicle_agency.status,
                    }
                else:
                    agency_data = {}

                # Get Vehicle Images
                images = Vehicle_Image.objects.filter(vehicle_master=vehicle)
                image_urls = [request.build_absolute_uri(img.image.url) for img in images]

                # Get Latest Booking (if any)
                current_order = (
                    LeaseOrderMaster.objects.filter(vehicle=vehicle)
                    .select_related("order_status")
                    .order_by('-created_at')
                    .first()
                )
                order_status_data = (
                    {
                        "id": str(current_order.order_status.id),
                        "status_name": current_order.order_status.order_status_name,
                        "start_date": current_order.start_date,
                        "end_date": current_order.end_date,
                    }
                    if current_order else None
                )

                return {
                    "id": str(vehicle.id),
                    "plate_number": vehicle.plate_number,
                    "vehicle_make": vehicle.vehicle_make,
                    "vehicle_model": vehicle.vehicle_model,
                    "body_type": vehicle.body_type,
                    "mfg_year": vehicle.mfg_year,
                    "lease_price_per_day": vehicle.lease_price_per_day,
                    "vehicle_status": vehicle.vehicle_status.vehicle_status_name,
                    "primary_location": vehicle.primary_location,
                    "owner": owner_data,
                    "agency": agency_data,
                    "order_status": order_status_data,
                    "images": image_urls,
                    "passenger_count": vehicle.passenger_count,
                    "created_at": vehicle.created_at,
                }

            data = [format_vehicle(v) for v in vehicles]

            return Response({
                "status": 1,
                "message": "Available active vehicles fetched successfully",
                "count": len(data),
                "data": data
            }, status=200)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong while fetching vehicles",
                "error": str(e)
            }, status=200)


# ======================================   CREATE BOOKING API   ======================================= #
# class CreateBookingLeaseOrderAPI(APIView):
#     @swagger_auto_schema(
#         security=[{'Bearer': []}],
#         operation_description="Create a new lease order when a rider books a vehicle (full model fields included)",
#         request_body=openapi.Schema(
#             type=openapi.TYPE_OBJECT,
#             required=[
#                 "user_id", "vehicle_id", "lease_type",
#                 "start_date", "end_date", "delivery_address"
#             ],
#             properties={
#                 "user_id": openapi.Schema(type=openapi.TYPE_STRING, description="UUID of Rider user"),
#                 "vehicle_id": openapi.Schema(type=openapi.TYPE_STRING, description="UUID of Vehicle_Master"),
#                 "agency_id": openapi.Schema(type=openapi.TYPE_STRING, description="UUID of Lease_Agency_Master"),
#                 "order_status_name": openapi.Schema(type=openapi.TYPE_STRING, description="Name of Order Status (optional)"),
#                 "purpose": openapi.Schema(type=openapi.TYPE_STRING, description="Purpose for booking"),
#                 "state": openapi.Schema(type=openapi.TYPE_STRING, description="State of booking"),
#                 "lease_type": openapi.Schema(type=openapi.TYPE_STRING, description="chauffeur or self_drive"),
#                 "leased_for": openapi.Schema(type=openapi.TYPE_STRING, description="e.g., Family, Business"),
#                 "start_date": openapi.Schema(type=openapi.TYPE_STRING, description="Lease start date"),
#                 "end_date": openapi.Schema(type=openapi.TYPE_STRING, description="Lease end date"),
#                 "client_location": openapi.Schema(type=openapi.TYPE_STRING, description="Client location"),
#                 "delivery_address": openapi.Schema(type=openapi.TYPE_STRING, description="Delivery address"),
#                 "delivery_distance_km": openapi.Schema(type=openapi.TYPE_NUMBER, description="Distance in KM"),
#                 "estimated_delivery_cost": openapi.Schema(type=openapi.TYPE_NUMBER, description="Delivery cost estimate"),
#                 "daily_price": openapi.Schema(type=openapi.TYPE_NUMBER, description="Daily price for vehicle"),
#                 "driver_id": openapi.Schema(type=openapi.TYPE_STRING, description="UUID of driver (optional)", nullable=True),
#             },
#         ),
#     )
#     def post(self, request):
#         try:
#             data = request.data

#             # Step 1: Validate required fields
#             required_fields = ["user_id", "vehicle_id", "lease_type", "start_date", "end_date", "delivery_address"]
#             for field in required_fields:
#                 if not data.get(field):
#                     return Response({"status": 0, "message": f"Missing required field: {field}", "data": None}, status=200)

#             # Step 2: Validate user & vehicle
#             user = User_Master.objects.filter(id=data.get("user_id")).first()
#             if not user:
#                 return Response({"status": 0, "message": "Invalid user_id", "data": None}, status=200)

#             vehicle = Vehicle_Master.objects.filter(id=data.get("vehicle_id")).first()
#             if not vehicle:
#                 return Response({"status": 0, "message": "Invalid vehicle_id", "data": None}, status=200)

#             # Parse dates safely
#             start_date = date.fromisoformat(data.get("start_date"))
#             end_date = date.fromisoformat(data.get("end_date"))

#             total_days = (end_date - start_date).days
#             if total_days <= 0:
#                 return Response({"status": 0, "message": "Invalid date range", "data": None}, status=200)

#             # Step 3: Prevent overlapping booking
#             overlap = LeaseOrderMaster.objects.filter(
#                 vehicle=vehicle,
#                 order_status__order_status_name__in=["new_order", "in_review", "confirm"],
#                 start_date__lte=end_date,
#                 end_date__gte=start_date
#             ).exists()
#             if overlap:
#                 return Response({"status": 0, "message": "This vehicle is already booked for the selected dates", "data": None}, status=200)

#             # Step 4: Get agency & order status
#             if data.get("agency_id"):
#                 agency = Lease_Agency_Master.objects.filter(id=data.get("agency_id")).first()
#             else:
#                 agency_relation = Vehicle_Agency.objects.filter(vehicle_master=vehicle, status="Active").first()
#                 agency = agency_relation.lease_agency if agency_relation else None

#             order_status_name = data.get("order_status_name", "new_order")
#             order_status = OrderStatusMaster.objects.filter(order_status_name__iexact=order_status_name).first()
#             if not order_status:
#                 return Response({"status": 0, "message": f"Invalid order_status_name: {order_status_name}", "data": None}, status=200)

#             # Step 5: Cost calculation (safe handling)
#             lease_price_per_day = (
#                 float(data.get("daily_price"))
#                 if data.get("daily_price") is not None
#                 else float(vehicle.lease_price_per_day or 0)
#             )

#             delivery_distance_km = float(data.get("delivery_distance_km") or 0)
#             estimated_delivery_cost = float(data.get("estimated_delivery_cost") or 0)

#             # Auto-calculate delivery if not provided
#             if delivery_distance_km == 0 or estimated_delivery_cost == 0:
#                 delivery_distance_km = get_distance_km(vehicle.primary_location, data.get("delivery_address"))
#                 estimated_delivery_cost = round(delivery_distance_km * 200, 2)

#             total_amount = round((lease_price_per_day * total_days) + estimated_delivery_cost, 2)

#             # Step 6: Create the lease order
#             lease_order = LeaseOrderMaster.objects.create(
#                 user=user,
#                 vehicle=vehicle,
#                 agency=agency,
#                 order_status=order_status,
#                 purpose=data.get("purpose"),
#                 state=data.get("state"),
#                 lease_type=data.get("lease_type"),
#                 leased_for=data.get("leased_for"),
#                 start_date=start_date,
#                 end_date=end_date,
#                 total_days=total_days,
#                 client_location=data.get("client_location"),
#                 delivery_address=data.get("delivery_address"),
#                 delivery_distance_km=delivery_distance_km,
#                 estimated_delivery_cost=estimated_delivery_cost,
#                 total_amount=total_amount,
#                 no_of_passenger = data.get("no_of_passenger"),
#                 driver_id=data.get("driver_id"),               
#             )

#             # vehicle.status = "attention"
#             # vehicle.save(update_fields=["status"])

#             # Step 7: Success response
#             return Response({
#                 "status": 1,
#                 "message": "Order created successfully, Your Order is in review",
#                 "data": {
#                     "order_id": str(lease_order.lease_order_id),
#                     "order_number": lease_order.order_number,
#                     "order_status": order_status.order_status_name,
#                     "vehicle": vehicle.vehicle_model,
#                     "total_days": total_days,
#                     "daily_price": lease_price_per_day,
#                     "delivery_cost": estimated_delivery_cost,
#                     "total_amount": total_amount,
#                     "distance_km": delivery_distance_km
#                 }
#             }, status=200)

#         except Exception as e:
#             return Response({
#                 "status": 0,
#                 "message": "Error creating order",
#                 "error": str(e),
#                 "data": None
#             }, status=200)

class CreateBookingLeaseOrderAPI(APIView):
    @swagger_auto_schema(
        security=[{'Bearer': []}],
        operation_description="Create a new lease order when a rider books a vehicle",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["user_id", "vehicle_id", "lease_type",
                      "start_date", "end_date", "delivery_address"],
            properties={
                "user_id": openapi.Schema(type=openapi.TYPE_STRING),
                "vehicle_id": openapi.Schema(type=openapi.TYPE_STRING),
                "agency_id": openapi.Schema(type=openapi.TYPE_STRING),
                "purpose": openapi.Schema(type=openapi.TYPE_STRING),
                "state": openapi.Schema(type=openapi.TYPE_STRING),
                "lease_type": openapi.Schema(type=openapi.TYPE_STRING),
                "leased_for": openapi.Schema(type=openapi.TYPE_STRING),
                "start_date": openapi.Schema(type=openapi.TYPE_STRING),
                "end_date": openapi.Schema(type=openapi.TYPE_STRING),
                "client_location": openapi.Schema(type=openapi.TYPE_STRING),
                "delivery_address": openapi.Schema(type=openapi.TYPE_STRING),
                "delivery_distance_km": openapi.Schema(type=openapi.TYPE_NUMBER),
                "estimated_delivery_cost": openapi.Schema(type=openapi.TYPE_NUMBER),
                "daily_price": openapi.Schema(type=openapi.TYPE_NUMBER),
                "no_of_passenger": openapi.Schema(type=openapi.TYPE_NUMBER),
                "driver_id": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
            },
        ),
    )
    def post(self, request):
        try:
            data = request.data

            # ---------------------------
            # STEP 1: Required Fields Check
            # ---------------------------
            required_fields = [
                "user_id", "vehicle_id", "lease_type",
                "start_date", "end_date", "delivery_address"
            ]
            for field in required_fields:
                if not data.get(field):
                    return Response(
                        {"status": 0, "message": f"Missing required field: {field}", "data": None},
                        status=200
                    )

            # ---------------------------
            # STEP 2: Validate User
            # ---------------------------
            user = User_Master.objects.filter(id=data.get("user_id")).first()
            if not user:
                return Response({"status": 0, "message": "Invalid user_id"}, status=200)

            # ---------------------------
            # STEP 3: Validate Vehicle
            # ---------------------------
            vehicle = Vehicle_Master.objects.filter(id=data.get("vehicle_id")).first()
            if not vehicle:
                return Response({"status": 0, "message": "Invalid vehicle_id"}, status=200)

            # Vehicle must be ACTIVE
            if not vehicle.active:
                return Response({
                    "status": 0,
                    "message": "Vehicle is not active"
                }, status=200)

            # Vehicle must be IDLE
            if not vehicle.vehicle_status or vehicle.vehicle_status.vehicle_status_name.lower() != "idle":
                return Response({
                    "status": 0,
                    "message": "Vehicle is not available (not idle)"
                }, status=200)

            # ---------------------------
            # STEP 4: Date Parsing
            # ---------------------------
            start_date = date.fromisoformat(data.get("start_date"))
            end_date = date.fromisoformat(data.get("end_date"))

            total_days = (end_date - start_date).days
            if total_days <= 0:
                return Response({"status": 0, "message": "Invalid date range"}, status=200)

            # ---------------------------
            # STEP 5: Prevent Overlapping Booking
            # ---------------------------
            BLOCKED_STATUSES = [
                "new_order", "owner_review", "confirmation",
                "invoiced", "invoice_processing", "invoice_paid",
                "scheduled", "active"
            ]

            overlap = LeaseOrderMaster.objects.filter(
                vehicle=vehicle,
                order_status__order_status_name__in=BLOCKED_STATUSES,
                start_date__lte=end_date,
                end_date__gte=start_date
            ).exists()

            if overlap:
                return Response({
                    "status": 0,
                    "message": "This vehicle is already booked for the selected dates"
                }, status=200)

            # ---------------------------
            # STEP 6: Agency Detection
            # ---------------------------
            if data.get("agency_id"):
                agency = Lease_Agency_Master.objects.filter(id=data.get("agency_id")).first()
            else:
                agency_relation = Vehicle_Agency.objects.filter(
                    vehicle_master=vehicle, status="Active"
                ).first()
                agency = agency_relation.lease_agency if agency_relation else None

            # ---------------------------
            # STEP 7: Order Status
            # default new_order
            # ---------------------------
            order_status_name = "new_order"
            order_status = OrderStatusMaster.objects.filter(
                order_status_name__iexact=order_status_name
            ).first()

            if not order_status:
                return Response({
                    "status": 0,
                    "message": f"Invalid order_status_name: {order_status_name}"
                }, status=200)

            # ---------------------------
            # STEP 8: Cost Calculation
            # ---------------------------
            lease_price_per_day = (
                float(data.get("daily_price"))
                if data.get("daily_price") is not None
                else float(vehicle.lease_price_per_day or 0)
            )

            delivery_distance_km = float(get_distance_km(vehicle.primary_location, data.get("delivery_address")))
            delivery_cost_per_km = settings.SET_KM_PRICE
            estimated_delivery_cost = round(delivery_distance_km * delivery_cost_per_km, 2)

            # if delivery_distance_km == 0 or estimated_delivery_cost == 0:
            #     delivery_distance_km = get_distance_km(vehicle.primary_location, data.get("delivery_address"))
            #     estimated_delivery_cost = round(delivery_distance_km * 200, 2)

            total_amount = round((lease_price_per_day * total_days) + estimated_delivery_cost, 2)

            # ---------------------------
            # STEP 9: Create Order
            # ---------------------------
            lease_order = LeaseOrderMaster.objects.create(
                user=user,
                vehicle=vehicle,
                agency=agency,
                order_status=order_status,
                purpose=data.get("purpose"),
                state=data.get("state"),
                lease_type=data.get("lease_type"),
                leased_for=data.get("leased_for"),
                start_date=start_date,
                end_date=end_date,
                total_days=total_days,
                client_location=data.get("client_location"),
                delivery_address=data.get("delivery_address"),
                delivery_distance_km=delivery_distance_km,
                estimated_delivery_cost=estimated_delivery_cost,
                total_amount=total_amount,
                no_of_passenger=data.get("no_of_passenger"),
                driver_id=data.get("driver_id"),
            )
            # formatted = locale.format_string("%d", lease_order.total_amount, grouping=True)
            formatted = f"{lease_order.total_amount:,.0f}"
            send_email(email_type="new_request",to_email=lease_order.agency.user_id.email,context={"lease_order": lease_order, "total_amount": formatted})
            # ---------------------------
            # STEP 10: Success Response
            # ---------------------------
            return Response({
                "status": 1,
                "message": "Order created successfully. Your order is in review.",
                "data": {
                    "order_id": str(lease_order.lease_order_id),
                    "order_number": lease_order.order_number,
                    "order_status": order_status.order_status_name,
                    "vehicle_make": vehicle.vehicle_make,
                    "vehicle_model": vehicle.vehicle_model,
                    "total_days": total_days,
                    "daily_price": lease_price_per_day,
                    "delivery_cost": estimated_delivery_cost,
                    "total_amount": total_amount,
                    "distance_km": delivery_distance_km
                }
            }, status=200)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Error creating order",
                "error": str(e),
                "data": None
            }, status=200)


# ======================================   GET ALL ORDER DETAILS API   ======================================= #

class GetOrderDetailAPI(APIView):
    # permission_classes = [IsAuthenticated]
    @swagger_auto_schema(
        # security=[{'Bearer': []}],
        operation_summary="Get Lease Order Details",
        operation_description=(
            "Fetch a single lease order by ID (path or query param), "
            "or fetch all orders filtered by user, agency, status, lease type, and date range."
        ),
        manual_parameters=[
            openapi.Parameter(
                "lease_order_id",
                openapi.IN_QUERY,
                description="Lease Order ID (UUID) - Optional if using query params",
                type=openapi.TYPE_STRING
            ),
        ],
        responses={
            1: openapi.Response(
                description="Order details fetched successfully",
                examples={
                    "application/json": {
                        "status": 1,
                        "message": "Order details fetched successfully",
                        "data": {"lease_order_id": "uuid", "user": {}, "vehicle": {}, "agency": {}, "order_status": {}}
                    }
                }
            ),
            0: openapi.Response(
                description="No orders found for given filters or ID",
                examples={
                    "application/json": {"status": 0, "message": "Order not found for the given lease_order_id", "data": None}
                }
            ),
            0: openapi.Response(
                description="Internal Server Error",
                examples={
                    "application/json": {"status": 0, "message": "Something went wrong while fetching orders", "error": "Error details", "data": None}
                }
            ),
        }
    )
    def get(self, request, lease_order_id=None):
        try:
            lease_id = lease_order_id or request.query_params.get("lease_order_id")
            auto_cancel_timeout_orders()
            if lease_id:
                lease_order = LeaseOrderMaster.objects.select_related(
                    "user", "vehicle", "agency", "order_status"
                ).filter(lease_order_id=lease_id).first()

                if not lease_order:
                    return Response({"status": 0, "message": "Order not found for the given lease_order_id", "data": None}, status=200)

                serializer = OrderSerializer(lease_order)
                return Response({"status": 1, "message": "Order details fetched successfully", "data": serializer.data}, status=200)

            filters = Q()
            user_id = request.query_params.get("user")
            agency_id = request.query_params.get("agency_id")
            status_name = request.query_params.get("order_status_name")
            lease_type = request.query_params.get("lease_type")
            start_date = request.query_params.get("start_date")
            end_date = request.query_params.get("end_date")

            if user_id:
                filters &= Q(user__id=user_id)
            if agency_id:
                filters &= Q(agency__id=agency_id)
            if status_name:
                # Update filter to use order_status_name
                filters &= Q(order_status__order_status_name__iexact=status_name)
            if lease_type:
                filters &= Q(lease_type__iexact=lease_type)
            if start_date and end_date:
                filters &= Q(start_date__gte=start_date, end_date__lte=end_date)

            lease_orders = LeaseOrderMaster.objects.select_related(
                "user", "vehicle", "agency", "order_status"
            ).filter(filters).order_by("-created_at")

            if not lease_orders.exists():
                return Response({"status": 0, "message": "No orders found for the applied filters", "data": None}, status=200)

            serializer = OrderSerializer(lease_orders, many=True)
            return Response({"status": 1, "message": f"{lease_orders.count()} orders fetched successfully", "data": serializer.data}, status=200)

        except Exception as e:
            return Response({"status": 0, "message": "Something went wrong while fetching orders", "error": str(e), "data": None}, status=200)


# ======================================   AGENCY UPDATE LEASE ORDER API   ======================================= #
#  AGENCY HAVE REQUEST IN [NEW_ORDERR] STATUS PART AGENCY MOVE TO [IN_REVIEW] STATEUS
class AgencyUpdateLeaseOrderAPI(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        security=[{'Bearer': []}],
        operation_summary="Update Lease Order (Agency)",
        operation_description="Allows an agency to move order from 'new_order' → 'owner_review'.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["order_id", "new_status"],
            properties={
                "order_id": openapi.Schema(type=openapi.TYPE_STRING),
                "new_status": openapi.Schema(type=openapi.TYPE_STRING),
                "purpose": openapi.Schema(type=openapi.TYPE_STRING),
                "lease_type": openapi.Schema(type=openapi.TYPE_STRING),
                "leased_for": openapi.Schema(type=openapi.TYPE_STRING),
                "client_location": openapi.Schema(type=openapi.TYPE_STRING),
                "delivery_address": openapi.Schema(type=openapi.TYPE_STRING),
                "delivery_distance_km": openapi.Schema(type=openapi.TYPE_NUMBER),
                "estimated_delivery_cost": openapi.Schema(type=openapi.TYPE_NUMBER),
                "start_date": openapi.Schema(type=openapi.TYPE_STRING, format="date-time"),
                "end_date": openapi.Schema(type=openapi.TYPE_STRING, format="date-time"),
            },
            example={
                "order_id": "uuid",
                "new_status": "owner_review",
                "purpose": "Pickup",
                "lease_type": "Self-drive",
                "leased_for": "Business"
            }
        )
    )
    def patch(self, request):
        try:
            order_id = request.data.get("order_id")
            new_status = request.data.get("new_status")

            # Validate inputs
            if not order_id or not new_status:
                return Response({
                    "status": 0,
                    "message": "Both 'order_id' and 'new_status' are required.",
                    "data": None
                }, status=200)

            # Fetch order
            lease_order = (
                LeaseOrderMaster.objects
                .select_related("order_status")
                .filter(lease_order_id=order_id)
                .first()
            )
            if not lease_order:
                return Response({
                    "status": 0,
                    "message": "Lease order not found for the given order_id.",
                    "data": None
                }, status=200)

            current_status = lease_order.order_status.order_status_name.lower()

            # ------------------------------
            #  ALLOWED TRANSITION:
            #  new_order ➝ owner_review
            # ------------------------------
            if current_status != "new_order":
                return Response({
                    "status": 0,
                    "message": f"Order cannot be updated from '{current_status}' by agency.",
                    "data": None
                }, status=200)

            # Validate new status object
            try:
                new_status_obj = OrderStatusMaster.objects.get(
                    order_status_name__iexact=new_status
                )
            except OrderStatusMaster.DoesNotExist:
                return Response({
                    "status": 0,
                    "message": "Invalid status name.",
                    "data": None
                }, status=200)

            new_status_name = new_status_obj.order_status_name.lower()

            if new_status_name != "owner_review":
                return Response({
                    "status": 0,
                    "message": "Invalid transition. Agency can only move to 'owner_review'.",
                    "data": None
                }, status=200)

            # Update editable fields (if provided)
            editable_fields = [
                "purpose", "lease_type", "leased_for", "client_location",
                "delivery_address", "delivery_distance_km", "estimated_delivery_cost",
                "start_date", "end_date"
            ]

            for field in editable_fields:
                if field in request.data:
                    setattr(lease_order, field, request.data[field])

            # Save new status
            lease_order.order_status = new_status_obj
            lease_order.updated_at = datetime.now()
            lease_order.save()
            # formatted = locale.format_string("%d", lease_order.total_amount, grouping=True)
            formatted = f"{lease_order.total_amount:,.0f}"
            send_email(email_type="order_confirmation",to_email=lease_order.vehicle.vehicle_owner.user_id.email,context={"lease_order": lease_order,"total_amount": formatted})
            return Response({
                "status": 1,
                "message": "Order moved to owner_review successfully by agency.",
                "data": {
                    "order_id": str(lease_order.lease_order_id),
                    "previous_status": current_status,
                    "new_status_id": str(new_status_obj.id),
                    "new_status_name": new_status_obj.order_status_name,
                    "updated_at": lease_order.updated_at,
                }
            }, status=200)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong while updating order status (Agency).",
                "error": str(e),
                "data": None
            }, status=200)

# class AgencyUpdateLeaseOrderAPI(APIView):
#     permission_classes = [IsAuthenticated]

#     @swagger_auto_schema(
#         operation_summary="Update Lease Order (Agency)",
#         operation_description="Allows an agency to update order details and status.",
#         request_body=openapi.Schema(
#             type=openapi.TYPE_OBJECT,
#             required=["order_id", "new_status"],
#             properties={
#                 "order_id": openapi.Schema(type=openapi.TYPE_STRING),
#                 "new_status": openapi.Schema(type=openapi.TYPE_STRING),
#                 "purpose": openapi.Schema(type=openapi.TYPE_STRING),
#                 "lease_type": openapi.Schema(type=openapi.TYPE_STRING),
#                 "leased_for": openapi.Schema(type=openapi.TYPE_STRING),
#                 "client_location": openapi.Schema(type=openapi.TYPE_STRING),
#                 "delivery_address": openapi.Schema(type=openapi.TYPE_STRING),
#                 "delivery_distance_km": openapi.Schema(type=openapi.TYPE_NUMBER),
#                 "estimated_delivery_cost": openapi.Schema(type=openapi.TYPE_NUMBER),
#                 "start_date": openapi.Schema(type=openapi.TYPE_STRING, format="date-time"),
#                 "end_date": openapi.Schema(type=openapi.TYPE_STRING, format="date-time"),
#             },
#             example={
#                 "order_id": "uuid",
#                 "new_status": "in_review",
#                 "purpose": "Airport pickup",
#                 "lease_type": "Chauffeur-driven",
#                 "leased_for": "Family",
#                 "client_location": "Rivers, Port Harcourt",
#                 "delivery_address": "103",
#                 "delivery_distance_km": 25,
#                 "estimated_delivery_cost": 62000,
#                 "start_date": "2025-10-29T12:00:00",
#                 "end_date": "2025-11-01T12:00:00"
#             }
#         )
#     )
#     def patch(self, request):
#         try:
#             order_id = request.data.get("order_id")
#             new_status = request.data.get("new_status")

#             if not order_id or not new_status:
#                 return Response({
#                     "status": 0,
#                     "message": "Both 'order_id' and 'new_status' are required.",
#                     "data": None
#                 }, status=200)

#             lease_order = LeaseOrderMaster.objects.filter(lease_order_id=order_id).first()
#             if not lease_order:
#                 return Response({
#                     "status": 0,
#                     "message": "Lease order not found for the given order_id.",
#                     "data": None
#                 }, status=200)

#             # Update editable fields
#             for field in [
#                 "purpose", "lease_type", "leased_for", "client_location",
#                 "delivery_address", "delivery_distance_km", "estimated_delivery_cost",
#                 "start_date", "end_date"
#             ]:
#                 if field in request.data:
#                     setattr(lease_order, field, request.data[field])

#             # Update status
#             new_status_obj = OrderStatusMaster.objects.filter(
#                 order_status_name__iexact=new_status
#             ).first()
#             if new_status_obj:
#                 lease_order.order_status = new_status_obj

#             lease_order.save()

#             # Serialize updated order
#             order_data = OrderDetailsSerializer(lease_order, context={"request": request}).data

#             return Response({
#                 "status": 1,
#                 "message": "Lease order updated successfully.",
#                 "data": order_data
#             }, status=200)

#         except Exception as e:
#             return Response({
#                 "status": 0,
#                 "message": "Something went wrong while updating lease order (Agency).",
#                 "error": str(e),
#                 "data": None
#             }, status=200)



# =============================================== GET AGENCY ORDERS API ================================================ #
#  Fetch all orders for the logged-in lease agency that are currently in "in_review" status.

class GetAgencyOrder(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        security=[{'Bearer': []}],
        operation_summary="Get In-Review Orders for Logged-in Vehicle Owner (Filtered by Agency)",
        operation_description=(
            "Fetch all 'in_review' orders for the **logged-in vehicle owner**, "
            "filtered by the provided `agency_id`."
        ),
        manual_parameters=[
            openapi.Parameter(
                'agency_id',
                openapi.IN_QUERY,
                description="Lease Agency ID (UUID)",
                type=openapi.TYPE_STRING,
                required=True
            )
        ],
        responses={200: openapi.Response(description="List of 'owner_review' orders for the logged-in owner and agency")}
    )
    def get(self, request):
        try:
            #Extract agency_id from request
            agency_id = request.query_params.get("agency_id") or request.data.get("agency_id")
            if not agency_id:
                return Response({
                    "status": 0,
                    "message": "Agency ID is required.",
                    "data": []
                }, status=200)

            # Identify the logged-in vehicle owner via JWT user
            owner = Vehicle_Owner_Master.objects.filter(user_id=request.user).first()
            if not owner:
                return Response({
                    "status": 0,
                    "message": "Vehicle owner profile not found for this logged-in user.",
                    "data": []
                }, status=200)
            
            owner_id = owner.id

            #  Get all vehicles for this owner
            vehicles = Vehicle_Master.objects.filter(vehicle_owner=owner_id)
            if not vehicles.exists():
                return Response({
                    "status": 0,
                    "message": "No vehicles found for this owner.",
                    "data": []
                }, status=200)

            vehicle_ids = [v.id for v in vehicles]

            #  Get in_review status
            in_review_status = OrderStatusMaster.objects.filter(order_status_name__iexact="owner_review").first()
            if not in_review_status:
                return Response({
                    "status": 0,
                    "message": "Order status 'owner_review' not found.",
                    "data": []
                }, status=200)

            # Fetch orders that match owner’s vehicles + agency + in_review
            orders = (
                LeaseOrderMaster.objects
                .select_related("vehicle", "agency", "order_status")
                .filter(
                    vehicle_id__in=vehicle_ids,
                    agency_id=agency_id,
                    order_status=in_review_status
                )
                .order_by("-created_at")
            )

            if not orders.exists():
                return Response({
                    "status": 0,
                    "message": "No 'in_review' orders found for this owner and agency.",
                    "data": []
                }, status=200)

            #  Serialize all data
            data = []
            for order in orders:
                data.append({
                    "order": OrderSerializer(order, context={"request": request}).data,
                })

            #  Return success response
            return Response({
                "status": 1,
                "message": "owner_review orders fetched successfully for this owner and agency.",
                "total_orders": len(data),
                "data": data
            }, status=200)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong while fetching owner-agency orders.",
                "error": str(e),
                "data": None
            }, status=200)
       
# ======================================   OWNER CONFIRM LEASE ORDER API   ======================================= #
# AGENCY SEND THE REQUEST TO OWNER -> UPDATE STATUS [IN_REVIEW] TO [CONFIRM] OR [CANCELLED]

class OwnerConfirmLeaseOrderAPI(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        security=[{'Bearer': []}],
        operation_summary="Confirmation or Decline Order (Owner)",
        operation_description=(
            "Allows a vehicle owner to move an order from 'in_review' → 'confirmation' or 'owner_declined'."
        ),
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["order_id", "new_status"],
            properties={
                "order_id": openapi.Schema(type=openapi.TYPE_STRING),
                "new_status": openapi.Schema(type=openapi.TYPE_STRING),
                "driver_id": openapi.Schema(type=openapi.TYPE_STRING),

            },
            example={
                "order_id": "uuid",
                "new_status": "invoice"
            }
        ),
        responses={
            200: openapi.Response(
                description="Order updated",
                examples={
                    "application/json": {
                        "status": 1,
                        "message": "Order confirmed successfully",
                        "data": {
                            "order_id": "uuid",
                            "previous_status": "in_review",
                            "new_status_id": "uuid",
                            "new_status_name": "invoice",
                            "updated_at": "2025-10-30T10:00:00Z"
                        }
                    }
                }
            )
        }
    )

    def patch(self, request):
        try:
            order_id = request.data.get("order_id")
            new_status = request.data.get("new_status")
            driver_id = request.data.get("driver_id")

            # Validate required fields
            if not order_id or not new_status:
                return Response({
                    "status": 0,
                    "message": "Both 'order_id' and 'new_status' are required.",
                    "data": None
                }, status=200)

            # Fetch order
            lease_order = (
                LeaseOrderMaster.objects
                .select_related("order_status")
                .filter(lease_order_id=order_id)
                .first()
            )
            if not lease_order:
                return Response({
                    "status": 0,
                    "message": "Lease order not found for the given order_id.",
                    "data": None
                }, status=200)

            current_status = lease_order.order_status.order_status_name.lower()

            # Allow only "in_review"
            if current_status != "owner_review":
                return Response({
                    "status": 0,
                    "message": f"Order cannot be updated from '{current_status}' by owner.",
                    "data": None
                }, status=200)

            # Validate new status
            try:
                new_status_obj = OrderStatusMaster.objects.get(
                    order_status_name__iexact=new_status
                )
            except OrderStatusMaster.DoesNotExist:
                return Response({
                    "status": 0,
                    "message": "Invalid status name.",
                    "data": None
                }, status=200)

            new_status_name = new_status_obj.order_status_name.lower()

            if new_status_name not in ["confirmation", "owner_declined"]:
                return Response({
                    "status": 0,
                    "message": "Invalid transition. Owner can only move to 'confirmation' or 'owner_declined'.",
                    "data": None
                }, status=200)
            
            # --------------------------------------
            # OPTIONAL DRIVER ASSIGNMENT LOGIC
            # --------------------------------------
            if new_status_name == "confirmation" and driver_id:
                driver = Vehicle_Owner_Driver.objects.filter(
                    id=driver_id,
                    vehicle_owner=lease_order.vehicle.vehicle_owner
                ).first()

                if not driver:
                    return Response({
                        "status": 0,
                        "message": "Invalid driver_id. Driver does not belong to this vehicle owner.",
                        "data": None
                    }, status=200)
                
                active_orders = LeaseOrderMaster.objects.filter(
                    driver=driver,
                    order_status__order_status_name__in=["confirmation", "owner_declined", "invoiced","invoice_processing","rider_declined","scheduled","active"]
                ).exclude(lease_order_id=lease_order.lease_order_id)

                if active_orders.exists():
                    return Response({
                        "status": 0,
                        "message": "Driver is already assigned to another active order.",
                        "data": None
                    }, status=200)
                lease_order.driver = driver

            # Update order
            lease_order.order_status = new_status_obj
            lease_order.updated_at = datetime.now()
            lease_order.save()

            # Response message
            if new_status_name == "confirmation":
                message_note = "Order confirmed successfully by vehicle owner."
                # send_email(email_type="order_confirmation",to_email=lease_order.user.email,context={"lease_order": lease_order})
            elif new_status_name == "owner_declined":
                message_note = "Order declined successfully by vehicle owner."
                
            return Response({
                "status": 1,
                "message": message_note,
                "data": {
                    "order_id": str(lease_order.lease_order_id),
                    "previous_status": current_status,
                    "new_status_id": str(new_status_obj.id),
                    "new_status_name": new_status_obj.order_status_name,
                    "updated_at": lease_order.updated_at,
                }
            }, status=200)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong while updating order status (Owner).",
                "error": str(e)
            }, status=200)

# ======================================   GET STATUS DETAILS API   ======================================= #
# GET ORDER DETAILS STATUS WISE LIKE -> [NEW_ORDER],[IN_REVIEW],[CONFIRM]......ETC.

# class GetStatusDetailsAPI(APIView):
#     permission_classes = [IsAuthenticated]
#     @swagger_auto_schema(
#         security=[{'Bearer': []}],
#         operation_description="Fetch lease order details — by order_id, order_status, or all orders if no filters provided.",
#         manual_parameters=[
#             openapi.Parameter(
#                 "order_id", openapi.IN_QUERY, description="UUID of a specific lease order (optional)",
#                 type=openapi.TYPE_STRING
#             ),
#             openapi.Parameter(
#                 "order_status", openapi.IN_QUERY, description="UUID of order status (optional)",
#                 type=openapi.TYPE_STRING
#             ),
#         ],
#         responses={
#             1: openapi.Response(description="Order(s) fetched successfully"),
#             0: openapi.Response(description="Invalid or missing parameters"),
#             0: openapi.Response(description="No data found"),
#             0: openapi.Response(description="Internal server error"),   
#         }
#     )

#     def get(self, request, lease_order_id=None):
#         try:
#             order_id = lease_order_id or request.query_params.get("order_id")
#             order_status = request.query_params.get("order_status")

#             # Fetch a single order by ID
#             if order_id:
#                 lease_order = LeaseOrderMaster.objects.select_related(
#                     "user", "vehicle", "agency", "order_status"
#                 ).filter(lease_order_id=order_id).first()

#                 if not lease_order:
#                     return Response({ "status": 0,"message": f"No order found for order_id '{order_id}'.","data": None}, status=200)

#                 serializer = OrderSerializer(lease_order)
#                 return Response({"status": 1,"message": "Order fetched successfully by ID.","data": serializer.data }, status=200)

#             # Fetch orders by status ID
#             if order_status:
#                 orders = LeaseOrderMaster.objects.select_related(
#                     "user", "vehicle", "agency", "order_status"
#                 ).filter(order_status__order_status_name__iexact=order_status).order_by("-created_at")

#                 if not orders.exists():
#                     return Response({
#                         "status": 0,
#                         "message": f"No orders found for order status '{order_status}'.",
#                         "data":None
#                     }, status=200)

#                 serializer = OrderSerializer(orders, many=True, context={'request': request})
                
#                 return Response({
#                     "status": 1,
#                     "message": f"{orders.count()} order(s) fetched successfully for status ID '{order_status}'.",
#                     "data": serializer.data
#                 }, status=200)
                

#             orders = LeaseOrderMaster.objects.select_related("user", "vehicle", "agency", "order_status").all().order_by("-created_at")

#             if not orders.exists():
#                 return Response({"status": 0,"message": "No orders found in the system.", "data": [] }, status=200)

#             serializer = OrderSerializer(orders, many=True, context={'request': request})
#             return Response({
#                 "status": 1,
#                 "message": f"{orders.count()} total order(s) fetched successfully.",
#                 "data": serializer.data
#             }, status=200)
            

#         except Exception as e:
#             return Response({
#                 "status": 0,
#                 "message": "Something went wrong while fetching order data.",
#                 "error": str(e)
#             }, status=200)

class GetStatusDetailsAPI(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        security=[{'Bearer': []}],
        operation_description="Fetch order details based on role (Owner, Agency, Admin).",
        manual_parameters=[
            openapi.Parameter("order_id", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("order_status", openapi.IN_QUERY, type=openapi.TYPE_STRING),
        ]
    )
    def get(self, request, lease_order_id=None):
        try:

            user = request.user
            order_id = lease_order_id or request.query_params.get("order_id")
            order_status = request.query_params.get("order_status")

            # =====================================================
            # BASE QUERY
            # =====================================================
            base_query = LeaseOrderMaster.objects.select_related(
                "user", "vehicle", "agency", "order_status"
            )

            # =====================================================
            # ROLE 1: OWNER TOKEN
            # =====================================================
            if user.vehicle_owner_master_set.exists():
                owner = user.vehicle_owner_master_set.first()

                if owner:
                    vehicle_ids = Vehicle_Master.objects.filter(
                        vehicle_owner=owner
                    ).values_list("id", flat=True)

                    base_query = base_query.filter(vehicle_id__in=vehicle_ids)

            # =====================================================
            # ROLE 2: AGENCY TOKEN
            # =====================================================
            elif user.lease_agency_master_set.exists():
                agency = user.lease_agency_master_set.first()
                base_query = base_query.filter(agency=agency)

            # =====================================================
            # ROLE 3: ADMIN TOKEN (NO FILTER)
            # =====================================================
            else:
                pass

            # =====================================================
            # FILTER BY order_id
            # =====================================================
            if order_id:
                order = base_query.filter(lease_order_id=order_id).first()

                if not order:
                    return Response({
                        "status": 0,
                        "message": "Order not found.",
                        "data": None
                    }, status=200)

                serializer = OrderSerializer(order, context={'request': request})
                return Response({
                    "status": 1,
                    "message": "Order fetched by ID.",
                    "data": serializer.data
                }, status=200)

            # =====================================================
            # FILTER BY order_status
            # =====================================================
            if order_status:
                orders = base_query.filter(
                    order_status__order_status_name__iexact=order_status
                ).order_by("-created_at")

                if not orders.exists():
                    return Response({
                        "status": 0,
                        "message": "No orders with this status.",
                        "data": None
                    }, status=200)

                serializer = OrderSerializer(orders, many=True, context={'request': request})
                return Response({
                    "status": 1,
                    "message": "Orders fetched successfully.",
                    "data": serializer.data
                }, status=200)

            # =====================================================
            # FETCH ALL (with role filtering)
            # =====================================================
            orders = base_query.order_by("-created_at")

            if not orders.exists():
                return Response({
                    "status": 0,
                    "message": "No orders found.",
                    "data": None
                }, status=200)

            serializer = OrderSerializer(orders, many=True, context={'request': request})
            return Response({
                "status": 1,
                "message": f"{orders.count()} orders fetched.",
                "data": serializer.data,
                "Micro_Insurance": settings.MICRO_INSURANCE 
            }, status=200)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong.",
                "error": str(e)
            }, status=200)


class LogOut(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_description="Logout user by verifying access token (no blacklist). Client should delete token after successful logout.",
        manual_parameters=[
            openapi.Parameter(
                'Authorization',
                openapi.IN_HEADER,
                description="Bearer access token",
                type=openapi.TYPE_STRING,
                required=True,
            )
        ],
        responses={
            200: openapi.Response(description="Logout successful"),
            401: openapi.Response(description="Invalid or missing token"),
        }
    )
    def post(self, request):
        try:
            # Extract token from Authorization header
            auth_header = request.headers.get('Authorization', None)
            if not auth_header or not auth_header.startswith('Bearer '):
                return Response(
                    {"status": 0, "message": "Access token not provided."},
                    status=status.HTTP_200_OK
                )

            token_str = auth_header.split(' ')[1]

            #  Validate token using AccessToken class
            try:
                AccessToken(token_str)  # type: ignore # Will raise TokenError if invalid or expired
            except TokenError:
                return Response(
                    {"status": 0, "message": "Invalid or expired access token."},
                    status=status.HTTP_200_OK
                )

            # If valid, tell client to delete it locally
            return Response(
                {"status": 1, "message": "Logout successful."},
                status=status.HTTP_200_OK
            )

        except Exception as e:
            return Response(
                {"status": 0, "message": f"Logout failed: {str(e)}"},
                status=status.HTTP_200_OK
            )


class DashboardAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        auto_cancel_timeout_orders()
        auto_update_scheduled_orders_status()

        try:
            user_instance = User_Master.objects.get(id=user.id)
        except User_Master.DoesNotExist:
            return Response({"status": 0, "message": "User not found"}, status=404)

        # COMMON USER INFO
        user_info = {
            "first_name": user_instance.first_name,
            "last_name": user_instance.last_name,
            "profile": request.build_absolute_uri(user_instance.photo.url) if user_instance.photo else None,
            "email": user_instance.email,
        }
        user_info['user_id'] = user.id
        # ==========================================================
        # CHECK IF USER IS A LEASE AGENCY
        # ==========================================================
        agency_instance = Lease_Agency_Master.objects.filter(user_id=user_instance).first()
        if agency_instance:
            vehicles = Vehicle_Master.objects.filter(vehicle_agencies__lease_agency=agency_instance)
            all_orders = LeaseOrderMaster.objects.filter(agency=agency_instance)

            completed_orders = all_orders.filter(order_status__order_status_name="completed").count()
            active_orders = all_orders.filter(order_status__order_status_name__in=ORDER_TIMEOUT_STATUSES_AGENCY).count()
            new_requests = all_orders.filter(order_status__order_status_name="new_order").count()
            scheduled = all_orders.filter(order_status__order_status_name="scheduled").count()
            cars_in_maintenance = vehicles.filter(active=False).count()
            car_on_trip = all_orders.filter(order_status__order_status_name="active").count()
            car_available = vehicles.filter(active=True,vehicle_status__vehicle_status_name__iexact="idle").count()
            
            AGENCY_COMMISSION_RATE = settings.AGENCY_COMMISSION_RATE

            # today = date.today()
            # yesterday = today - timedelta(days=1)

            # orders_ended_yesterday = all_orders.filter(
            #     order_status__order_status_name="completed",
            #     end_date=yesterday
            # )
            today = date.today()
            # yesterday = today - timedelta(days=1)

            order_paid_agency = all_orders.filter(invoices__invoice_status__invoice_status_name__iexact="Paid",invoices__updated_at__date=today)

            earning = 0
            for order in order_paid_agency:
                lease_price = order.vehicle.lease_price_per_day or 0
                total_days = order.total_days or 0

                leasing_amount = lease_price * total_days
                earning += int(leasing_amount * AGENCY_COMMISSION_RATE)

            dashboard_data = {
                "rating": "0",
                "business_name": agency_instance.business_name,
                "completed_orders": completed_orders,
                "car_available": car_available,
                "car_on_trip": car_on_trip,
                "active_bookings": active_orders,
                "earnings": f"NGN {earning:,}",
                "new_requests": new_requests,
                "scheduled": scheduled,
                "cars_in_maintenance": cars_in_maintenance,
            }

            return Response({
                "status": 1,
                "message": "Lease Agency Dashboard fetched successfully",
                "data": {
                    "userinfo": user_info,
                    "lease_agency": {
                        "agency_id": str(agency_instance.id),
                        "business_name": agency_instance.business_name,
                        "phone": agency_instance.phone_number,
                        "email": agency_instance.business_Email,
                        "agency_profile": request.build_absolute_uri(agency_instance.agency_profile.url) if agency_instance.agency_profile else None
                    },
                    "dashboard_data": dashboard_data,
                }
            })

        # ==========================================================
        # OTHERWISE USER IS A VEHICLE OWNER
        # ==========================================================
        owners = Vehicle_Owner_Master.objects.filter(user_id=user_instance)
        if owners.exists():
            business_info = []
            all_lease_orders_in_review = LeaseOrderMaster.objects.none()
            all_lease_orders_booking = LeaseOrderMaster.objects.none()

            # Get order statuses
            try:
                in_review_status = OrderStatusMaster.objects.get(order_status_name="owner_review")
            except OrderStatusMaster.DoesNotExist:
                in_review_status = None

            try:
                booking_status = OrderStatusMaster.objects.get(order_status_name="invoice_paid")
            except OrderStatusMaster.DoesNotExist:
                booking_status = None

            for owner in owners:
                # Owner business info
                business_info.append({
                    "id": str(owner.id),
                    "full_name": owner.full_name,
                    "business_name": owner.business_name,
                    "business_Email": owner.business_Email,
                    "business_number": owner.business_number,
                    "phone_number": owner.phone_number,
                    "year": owner.year,
                    "state": owner.state,
                    "address": owner.address,
                })

                # Vehicles of this owner
                vehicles = Vehicle_Master.objects.filter(vehicle_owner=owner)

                # Lease orders in review
                lease_orders_in_review = LeaseOrderMaster.objects.filter(
                    vehicle__in=vehicles,
                    order_status=in_review_status
                )
                all_lease_orders_in_review |= lease_orders_in_review

                # Lease orders in booking (for schedules)
                lease_orders_booking = LeaseOrderMaster.objects.filter(
                    vehicle__in=vehicles,
                    order_status=booking_status
                )
                all_lease_orders_booking |= lease_orders_booking

            # Linked agencies for orders in review
            linked_agencies = []
            for agency in Lease_Agency_Master.objects.filter(lease_orders__in=all_lease_orders_in_review).distinct():
                linked_agencies.append({
                    "agency_id": str(agency.id),
                    "business_name": agency.business_name,
                    "phone": agency.phone_number,
                    "email": agency.business_Email,
                    "agency_profile": request.build_absolute_uri(agency.agency_profile.url) if getattr(agency, "agency_profile", None) else None,
                    "address": agency.address
                })

            # Schedules → linked agencies for orders with booking status
            schedules = []
            for agency in Lease_Agency_Master.objects.filter(lease_orders__in=all_lease_orders_booking).distinct():
                schedules.append({
                    "agency_id": str(agency.id),
                    "business_name": agency.business_name,
                    "phone": agency.phone_number,
                    "email": agency.business_Email,
                    "agency_profile": request.build_absolute_uri(agency.agency_profile.url) if getattr(agency, "agency_profile", None) else None,
                    "address": agency.address
                })

            vehicles_count = Vehicle_Master.objects.filter(vehicle_owner__in=owners)
            cars_listed = vehicles_count.count()
            cars_on_trip = LeaseOrderMaster.objects.filter(vehicle__in=vehicles_count, order_status__order_status_name="active").count()
            cars_idle = vehicles.filter(active=True,vehicle_status__vehicle_status_name__iexact="idle").count()

            cars_in_maintenance = vehicles_count.filter(active=False).count()

            completed_orders = LeaseOrderMaster.objects.filter(vehicle__in=vehicles_count, order_status__order_status_name="completed").count()
            earning = 0
            new_requests = LeaseOrderMaster.objects.filter(vehicle__in=vehicles_count, order_status__order_status_name="owner_review").count()
            scheduled_count = LeaseOrderMaster.objects.filter(vehicle__in=vehicles_count, order_status__order_status_name="scheduled").count()

            # for order in active_orders_for_earning:
            #     lease_price = order.vehicle.lease_price_per_day or 0
            #     earning += (lease_price * 0.70)


            OWNER_COMMISSION_RATE = settings.OWNER_COMMISSION_RATE

            # today = date.today()
            # yesterday = today - timedelta(days=1)

            # active_orders_for_earning = LeaseOrderMaster.objects.filter(vehicle__in=vehicles_count, order_status__order_status_name="completed",end_date=yesterday)
            today = date.today()
            # yesterday = today - timedelta(days=1)

            owners_paid_orders = LeaseOrderMaster.objects.filter(vehicle__in=vehicles_count, invoices__invoice_status__invoice_status_name__iexact="Paid",invoices__updated_at__date=today)

            earning = 0
            for order in owners_paid_orders:
                lease_price = order.vehicle.lease_price_per_day or 0
                total_days = order.total_days or 0

                leasing_amount = lease_price * total_days
                earning += int(leasing_amount * OWNER_COMMISSION_RATE)

            orders_this_month = LeaseOrderMaster.objects.filter(
                vehicle__in=vehicles_count,
                order_status__order_status_name="completed",
                end_date__year=today.year,
                end_date__month=today.month
            )

            monthly_earning = 0
            for order in orders_this_month:
                lease_price_per_day = order.vehicle.lease_price_per_day or 0
                total_days = order.total_days or 0
                monthly_earning += int(lease_price_per_day * total_days * OWNER_COMMISSION_RATE)

            # OWNER DASHBOARD DATA
            dashboard_data = {
                "rating": "0",
                "completed_orders": completed_orders,
                "car_available": cars_idle,
                "car_on_trip": cars_on_trip,
                "earnings": f"NGN {earning:,}",
                "monthly_earning": f"NGN {monthly_earning:,}",
                "new_requests": new_requests,
                "scheduled": scheduled_count,
                "cars_listed": cars_listed,
                "cars_idle": cars_idle,
                "cars_in_maintenance": cars_in_maintenance,
                "monthly_earnings": 0,
                "monthly_completed_rides": 0,
                "pending_payouts": 0,
                "payouts":0
            }

            return Response({
                "status": 1,
                "message": "Owner Dashboard fetched successfully",
                "data": {
                    "userinfo": user_info,
                    "business_info": business_info,
                    "linked_agencies": linked_agencies,
                    "dashboard_data": dashboard_data,
                    "schedules": schedules
                }
            })
        
        
        latest_order = (
            LeaseOrderMaster.objects
            .filter(user=user_instance)
            .select_related("vehicle", "agency", "order_status")
            .prefetch_related("vehicle__images")
            .order_by("-created_at")
            .first()
        )

        if latest_order:
            serialized_order = RiderOrderSerializer(
                latest_order, context={"request": request}
            ).data
        else:
            serialized_order = None
        
        return Response({
            "status": 1,
            "message": "Rider Dashboard fetched successfully",
            "data": {
                "userinfo": user_info,
                "latest_order": serialized_order
            }
        })
    
        # For ALL Order Data
        # user_orders = LeaseOrderMaster.objects.filter(user=user_instance).select_related(
        #     "vehicle", "order_status"
        # ).prefetch_related("vehicle__images")

        # serialized_orders = RiderOrderSerializer(
        #     user_orders, many=True, context={"request": request}
        # ).data

        # return Response({
        #     "status": 1,
        #     "message": "Rider Dashboard fetched successfully",
        #     "data": {
        #         "userinfo": user_info,
        #         "orders": serialized_orders,
        #     }
        # })




class GetOrdersByStatusAPI(APIView):

    @swagger_auto_schema(
        operation_summary="Get all orders by status name (no JWT required)",
        operation_description=(
            "Fetches all lease orders filtered by order status name (e.g. confirm, cancelled, in_review). "
            "Does not require authentication or owner ID."
        ),
        manual_parameters=[
            openapi.Parameter(
                'status_name',
                openapi.IN_QUERY,
                description="Order status name (e.g. confirm, cancelled, in_review)",
                type=openapi.TYPE_STRING,
                required=True
            )
        ],
        responses={200: openapi.Response(description="Orders filtered by status name")}
    )
    def get(self, request):
        try:
            #  Get status name from query
            status_name = request.query_params.get("status_name")
            if not status_name:
                return Response({
                    "status": 0,
                    "message": "Query parameter 'status_name' is required (e.g., confirm).",
                    "data": []
                }, status=200)

            # 🔹 Find status object
            order_status = OrderStatusMaster.objects.filter(order_status_name__iexact=status_name).first()
            if not order_status:
                return Response({
                    "status": 0,
                    "message": f"Order status '{status_name}' not found.",
                    "data": []
                }, status=200)

            # 🔹 Get all orders with that status
            orders = (
                LeaseOrderMaster.objects
                .select_related("vehicle", "agency", "order_status")
                .filter(order_status=order_status)
                .order_by("-created_at")
            )

            if not orders.exists():
                return Response({
                    "status": 0,
                    "message": f"No orders found with status '{status_name}'.",
                    "data": []
                }, status=200)

            # 🔹 Serialize the data
            data = [
                {
                    "order": OrderSerializer(order, context={"request": request}).data,
                    "vehicle": VehicleSerializer(order.vehicle, context={"request": request}).data,
                    "agency": LeaseAgencySerializer(order.agency, context={"request": request}).data,
                }
                for order in orders
            ]

            return Response({
                "status": 1,
                "message": f"Orders with status '{status_name}' fetched successfully.",
                "data": data
            }, status=200)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong while fetching orders by status.",
                "error": str(e),
                "data": None
            }, status=200)

class GetAgencyBookedOrder(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        security=[{'Bearer': []}],
        operation_summary="Get 'Booking' Orders for Logged-in Vehicle Owner (Filtered by Agency)",
        operation_description=(
            "Fetch all orders with status 'booking' for the **logged-in vehicle owner**, "
            "filtered by the provided `agency_id`."
        ),
        manual_parameters=[
            openapi.Parameter(
                'agency_id',
                openapi.IN_QUERY,
                description="Lease Agency ID (UUID)",
                type=openapi.TYPE_STRING,
                required=True
            )
        ],
        responses={200: openapi.Response(description="List of 'booking' orders for the logged-in owner and agency")}
    )
    def get(self, request):
        try:
            # Extract agency_id from request
            agency_id = request.query_params.get("agency_id") or request.data.get("agency_id")
            if not agency_id:
                return Response({
                    "status": 0,
                    "message": "Agency ID is required.",
                    "data": []
                }, status=200)

            # Identify the logged-in vehicle owner
            owner = Vehicle_Owner_Master.objects.filter(user_id=request.user).first()
            if not owner:
                return Response({
                    "status": 0,
                    "message": "Vehicle owner profile not found for this logged-in user.",
                    "data": []
                }, status=200)

            owner_id = owner.id

            # Get all vehicles for this owner
            vehicles = Vehicle_Master.objects.filter(vehicle_owner=owner_id)
            if not vehicles.exists():
                return Response({
                    "status": 0,
                    "message": "No vehicles found for this owner.",
                    "data": []
                }, status=200)

            vehicle_ids = [v.id for v in vehicles]

            # Get booking status
            booking_status = OrderStatusMaster.objects.filter(order_status_name__iexact="invoice_paid").first()
            if not booking_status:
                return Response({
                    "status": 0,
                    "message": "Order status 'invoice_paid' not found.",
                    "data": []
                }, status=200)

            # Fetch 'booking' orders that match owner’s vehicles and agency
            orders = (
                LeaseOrderMaster.objects
                .select_related("vehicle", "agency", "order_status")
                .filter(
                    vehicle_id__in=vehicle_ids,
                    agency_id=agency_id,
                    order_status=booking_status
                )
                .order_by("-created_at")
            )

            if not orders.exists():
                return Response({
                    "status": 0,
                    "message": "No 'booking' orders found for this owner and agency.",
                    "data": []
                }, status=200)

            # Serialize all data
            data = []
            for order in orders:
                data.append({
                    "order": OrderSerializer(order, context={"request": request}).data,
                })

            # Return success response
            return Response({
                "status": 1,
                "message": "'Booking' orders fetched successfully for this owner and agency.",
                "total_orders": len(data),
                "data": data
            }, status=200)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong while fetching owner-agency booking orders.",
                "error": str(e),
                "data": None
            }, status=200)
 


# class CreateInvoiceAPI(APIView):
#     permission_classes = [IsAuthenticated]

#     def post(self, request):
#         # Validate user is a Lease Agency
#         try:
#             agency = Lease_Agency_Master.objects.get(user_id=request.user)
#         except Lease_Agency_Master.DoesNotExist:
#             return Response(
#                 {"error": "Only Lease Agencies can create invoices."},
#                 status=status.HTTP_403_FORBIDDEN
#             )

#         data = request.data.copy()

#         # 1. Set default static status (Example: "pending")
#         try:
#             default_status = InvoiceStatusMaster.objects.get(invoice_status_name="Created")
#         except InvoiceStatusMaster.DoesNotExist:
#             return Response({"error": "Default invoice status not found in master."},
#                             status=status.HTTP_500_INTERNAL_SERVER_ERROR)

#         data["invoice_status"] = str(default_status.invoice_status_id)

#         serializer = LeaseInvoiceSerializer(data=data)

#         if serializer.is_valid():

#             lease_order = serializer.validated_data["lease_order"]

#             # Validate order belongs to agency
#             if lease_order.agency.id != agency.id:
#                 return Response(
#                     {"error": "You are not authorized to create an invoice for this order."},
#                     status=status.HTTP_403_FORBIDDEN
#                 )

#             # Prevent duplicate invoice
#             if LeaseInvoice.objects.filter(lease_order=lease_order).exists():
#                 return Response(
#                     {"error": "Invoice already exists for this order."},
#                     status=status.HTTP_400_BAD_REQUEST
#                 )

#             invoice = serializer.save()

#             return Response(
#                 {
#                     "message": "Invoice created successfully",
#                     "data": LeaseInvoiceSerializer(invoice).data
#                 },
#                 status=status.HTTP_201_CREATED
#             )

#         return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

# class CreateInvoiceAPI(APIView):
#     permission_classes = [IsAuthenticated]
#     @swagger_auto_schema(
#         security=[{'Bearer': []}],
#         operation_summary="Create Invoice for Lease Order (Lease Agency Only)",
#         operation_description=(
#             "Allows a **Lease Agency** to create an invoice for a specific lease order. "
#             "Automatically sets the default invoice status to `Created`. "
#             "Prevents duplicate invoices and ensures the order belongs to the logged-in agency."
#         ),
#         request_body=LeaseInvoiceSerializer,
#         responses={
#             200: openapi.Response(
#                 description="Invoice creation response",
#                 examples={
#                     "application/json": {
#                         "status": 1,
#                         "message": "Invoice created successfully",
#                         "data": {
#                             "invoice_id": "UUID",
#                             "lease_order": "UUID",
#                             "amount": 1200,
#                             "invoice_status": "Created"
#                         }
#                     }
#                 }
#             )
#         }
#     )
#     def post(self, request):
#         try:
#             # Validate lease agency user
#             try:
#                 agency = Lease_Agency_Master.objects.get(user_id=request.user)
#             except Lease_Agency_Master.DoesNotExist:
#                 return Response({
#                     "status": 0,
#                     "message": "Only Lease Agencies can create invoices.",
#                     "error": "Permission denied",
#                     "data": None
#                 }, status=200)

#             data = request.data.copy()

#             # Get default invoice status
#             try:
#                 default_status = InvoiceStatusMaster.objects.get(invoice_status_name="Created")
#             except InvoiceStatusMaster.DoesNotExist:
#                 return Response({
#                     "status": 0,
#                     "message": "Default invoice status not found.",
#                     "error": "Master missing",
#                     "data": None
#                 }, status=200)

#             data["invoice_status"] = str(default_status.invoice_status_id)

#             serializer = LeaseInvoiceSerializer(data=data)

#             if serializer.is_valid():

#                 lease_order = serializer.validated_data["lease_order"]

#                 # Check if order belongs to agency
#                 if lease_order.agency.id != agency.id:
#                     return Response({
#                         "status": 0,
#                         "message": "Not authorized to create invoice for this order.",
#                         "error": "Unauthorized access",
#                         "data": None
#                     }, status=200)

#                 # Prevent duplicate invoice
#                 if LeaseInvoice.objects.filter(lease_order=lease_order).exists():
#                     return Response({
#                         "status": 0,
#                         "message": "Invoice already exists for this order.",
#                         "error": "Duplicate invoice",
#                         "data": None
#                     }, status=200)

#                 # Save invoice
#                 invoice = serializer.save()

#                 try:
#                     invoiced_status = OrderStatusMaster.objects.get(order_status_name="invoiced")
#                     lease_order.order_status = invoiced_status
#                     lease_order.save()
#                 except OrderStatusMaster.DoesNotExist:
#                     return Response({
#                         "status": 0,
#                         "message": "Order status 'Invoiced' not found in master.",
#                         "error": "Missing master data",
#                         "data": None
#                     }, status=200)
#                 except Exception as e:
#                     return Response({
#                         "status": 0,
#                         "message": "Failed to update order status.",
#                         "error": str(e),
#                         "data": None
#                     }, status=200)

#                 return Response({
#                     "status": 1,
#                     "message": "Invoice created successfully",
#                     "data": LeaseInvoiceSerializer(invoice).data
#                 }, status=200)

#             # Validation error
#             return Response({
#                 "status": 0,
#                 "message": "Validation failed",
#                 "error": serializer.errors,
#                 "data": None
#             }, status=200)

#         except Exception as e:
#             return Response({
#                 "status": 0,
#                 "message": "Something went wrong while creating invoice.",
#                 "error": str(e),
#                 "data": None
#             }, status=200)
from decimal import Decimal

class CreateInvoiceAPI(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        security=[{'Bearer': []}],
        operation_summary="Create Invoice for a Lease Order (Lease Agency Only)",
        operation_description=(
            "Allows a **Lease Agency** to create an invoice for a lease order.\n\n"
            "📌 **IMPORTANT AUTOMATIONS:**\n"
            "- VAT (7.5%) is calculated automatically.\n"
            "- `total_amount = subtotal + VAT`.\n"
            "- Invoice status is set to **Created** by default.\n"
            "- Prevents duplicate invoices.\n"
            "- Ensures the order belongs to the logged-in agency.\n"
            "- Returns invoice data and **minimal order details** (order_id, order_number, dates, total_days, order_status)."
        ),
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["lease_order", "subtotal"],
            properties={
                "lease_order": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="UUID of the lease order"
                ),
                "micro_insurance": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="Micro-insurance amount"
                ),
                "subtotal": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="Base subtotal (VAT automatically calculated)"
                ),
                "delivery_cost": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="Delivery charges"
                ),
                "discount": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="Discount applied"
                ),
                "note": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="Extra invoice notes"
                ),
                "due_date": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    format="date",
                    description="Invoice due date (YYYY-MM-DD)"
                ),
            },
            example={
                "lease_order": "7ee49b4b-6d0a-480c-8575-5af90919c2a8",
                "micro_insurance": "200.00",
                "subtotal": "1500.00",
                "delivery_cost": "100.00",
                "discount": "50.00",
                "note": "Monthly rental invoice",
                "due_date": "2025-11-30"
            }
        ),
        responses={
            200: openapi.Response(
                description="Invoice creation response",
                examples={
                    "application/json": {
                        "status": 1,
                        "message": "Invoice created successfully",
                        "data": {
                            "invoice": {
                                "invoice_id": "c145917f-0e6d-483a-9b93-4d33e1201082",
                                "customer_name": "John Adamu",
                                "invoice_number": "SC01/002",
                                "micro_insurance": "200.00",
                                "subtotal": "1500.00",
                                "delivery_cost": "100.00",
                                "discount": "50.00",
                                "vat": "112.50",
                                "total_amount": "1612.50",
                                "note": "Monthly rental invoice",
                                "due_date": "2025-11-30",
                                "created_at": "2025-11-14T14:06:02.219Z",
                                "lease_order": "bd7763e6-df4c-463e-9ff1-6d93449f1455",
                                "invoice_status": "e56bd68c-7400-4cf5-92a2-cb7a05578609"
                            },
                            "order_details": {
                                "lease_order_id": "bd7763e6-df4c-463e-9ff1-6d93449f1455",
                                "order_number": "LO-2025-001",
                                "start_date": "2025-11-10T08:00:00Z",
                                "end_date": "2025-11-14T12:00:00Z",
                                "total_days": 4,
                                "order_status": "Invoiced"
                            }
                        }
                    },
                    "application/json (Duplicate Invoice)": {
                        "status": 0,
                        "message": "Invoice already exists for this order.",
                        "data": None
                    },
                    "application/json (Unauthorized)": {
                        "status": 0,
                        "message": "Only Lease Agencies can create invoices.",
                        "data": None
                    },
                    "application/json (Master Missing)": {
                        "status": 0,
                        "message": "Default invoice status not found.",
                        "data": None
                    },
                    "application/json (Validation Error)": {
                        "status": 0,
                        "message": "Validation failed",
                        "error": {
                            "subtotal": ["This field is required."]
                        }
                    }
                }
            )
        }
    )
    def post(self, request):
        try:
            # Validate agency
            try:
                agency = Lease_Agency_Master.objects.get(user_id=request.user)
            except Lease_Agency_Master.DoesNotExist:
                return Response({
                    "status": 0,
                    "message": "Only Lease Agencies can create invoices.",
                    "data": None
                }, status=200)

            try:
                invoiced_status = OrderStatusMaster.objects.get(order_status_name="invoiced")  
            except OrderStatusMaster.DoesNotExist:
                return Response({
                    "status": 0,
                    "message": "Order status 'invoiced' not found.",
                    "data": None
                })

            data = request.data.copy()

            # Calculate VAT (7.5%) and Total
            subtotal = Decimal(data.get("subtotal", 0))
            vat = subtotal * Decimal("0.075")
            total_amount = subtotal + vat

            data["vat"] = round(vat, 2)
            data["total_amount"] = round(total_amount, 2)

            # Default Invoice Status
            default_status = InvoiceStatusMaster.objects.get(invoice_status_name="Created")
            data["invoice_status"] = str(default_status.invoice_status_id)

            serializer = LeaseInvoiceSerializer(data=data)

            if serializer.is_valid():
                lease_order = serializer.validated_data["lease_order"]
                # Check agency ownership
                if lease_order.agency != agency:
                    return Response({
                        "status": 0,
                        "message": "Not authorized to create invoice for this order.",
                        "data": None
                    }, status=200)

                # Prevent duplicate invoice
                if LeaseInvoice.objects.filter(lease_order=lease_order).exists():
                    return Response({
                        "status": 0,
                        "message": "Invoice already exists for this order.",
                        "data": None
                    }, status=200)

                # Save invoice
                invoice = serializer.save()

                # Update order status to 'Invoiced'

                lease_order.order_status = invoiced_status
                lease_order.save()


                # Include order details
                order_details = {
                    "lease_order_id": str(lease_order.lease_order_id),
                    "order_number": lease_order.order_number,
                    "start_date": lease_order.start_date,
                    "end_date": lease_order.end_date,
                    "total_days": lease_order.total_days,
                    "order_status": lease_order.order_status.order_status_name if lease_order.order_status else None,
                    "agency_profile":request.build_absolute_uri(lease_order.agency.agency_profile.url) if getattr(agency, "agency_profile", None) else None,
                    "business_name":lease_order.agency.business_name
                }

                return Response({
                    "status": 1,
                    "message": "Invoice created successfully",
                    "data": {
                        "invoice": LeaseInvoiceSerializer(invoice).data,
                        "order_details": order_details
                    }
                }, status=200)

            return Response({
                "status": 0,
                "message": "Validation failed",
                "error": serializer.errors
            }, status=200)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong.",
                "error": str(e)
            }, status=200)

# class ListAgencyInvoicesAPI(APIView):
#     permission_classes = [IsAuthenticated]
#     @swagger_auto_schema(
#         security=[{'Bearer': []}],
#         operation_summary="List All Invoices for Logged-in Lease Agency",
#         operation_description=(
#             "Fetches all invoices created for orders belonging to the **logged-in lease agency**. "
#             "Results are sorted by newest first."
#         ),
#         responses={
#             200: openapi.Response(
#                 description="List of invoices for the logged-in lease agency",
#                 examples={
#                     "application/json": {
#                         "status": 1,
#                         "message": "Invoices fetched successfully",
#                         "count": 3,
#                         "data": [
#                             {
#                                 "invoice_id": "UUID",
#                                 "lease_order": "UUID",
#                                 "amount": 1200,
#                                 "invoice_status": "Created",
#                                 "created_at": "2025-02-10T12:30:00Z"
#                             }
#                         ]
#                     }
#                 }
#             )
#         }
#     )
#     def get(self, request):
#         try:
#             # Validate user is a Lease Agency
#             try:
#                 agency = Lease_Agency_Master.objects.get(user_id=request.user)
#             except Lease_Agency_Master.DoesNotExist:
#                 return Response({
#                     "status": 0,
#                     "message": "Only Lease Agencies can view invoices.",
#                     "error": "Permission denied",
#                     "data": None
#                 }, status=200)

#             # Fetch invoices for this agency
#             invoices = LeaseInvoice.objects.filter(
#                 lease_order__agency=agency
#             ).order_by("-created_at")
#             invoices_agency=[]
#             for invoice in invoices:

#                 vehicle_image = invoice.lease_order.vehicle.images.first()
#                 vehicle_image_url = (
#                     request.build_absolute_uri(vehicle_image.image.url)
#                     if vehicle_image else None
#                 )
#                 # Prepare full response data
#                 data = {
#                     "invoice_id": str(invoice.invoice_id),
#                     "invoice_number": invoice.invoice_number,
#                     "invoice_status": invoice.invoice_status.invoice_status_name,
#                     "micro_insurance": str(invoice.micro_insurance),
#                     "subtotal": str(invoice.subtotal),
#                     "delivery_cost": str(invoice.delivery_cost),
#                     "discount": str(invoice.discount),
#                     "vat": str(invoice.vat),
#                     "total_amount": str(invoice.total_amount),
#                     "note": invoice.note,
#                     "invoice_pdf": request.build_absolute_uri(invoice.invoice_pdf.url) if invoice.invoice_pdf else None,
#                     "due_date": str(invoice.due_date),
#                     "created_at": str(invoice.created_at),

#                     # Related data
#                     "customer_name": f"{invoice.lease_order.user.first_name} {invoice.lease_order.user.last_name}".strip(),
#                     "order_number": invoice.lease_order.order_number,
#                     "start_date": invoice.lease_order.start_date,
#                     "end_date": invoice.lease_order.end_date,
#                     "total_days":invoice.lease_order.total_days,
#                     "vehicle_make":invoice.lease_order.vehicle.vehicle_make,
#                     "vehicle_model":invoice.lease_order.vehicle.vehicle_model,
#                     "vin":invoice.lease_order.vehicle.vehicle_identify_number,
#                     "mfg_year":invoice.lease_order.vehicle.mfg_year,
#                     "lease_per_day":invoice.lease_order.vehicle.lease_price_per_day,
#                     "vehicle_total_amount": invoice.lease_order.vehicle.lease_price_per_day * invoice.lease_order.total_days,
#                     "agency_profile":request.build_absolute_uri(invoice.lease_order.agency.agency_profile.url) if getattr(invoice.lease_order.agency, "agency_profile", None) else None,
#                     "business_name":invoice.lease_order.agency.business_name,
#                     "vehicle_image_url": vehicle_image_url,
#                     "agency_phone_number": invoice.lease_order.agency.phone_number,
#                     "vehicle_plate_number": invoice.lease_order.vehicle.plate_number
#                 }
#                 invoices_agency.append(data)
#             # serializer = AllLeaseInvoiceSerializer(invoices, many=True)

#             return Response({
#                 "status": 1,
#                 "message": "Invoices fetched successfully",
#                 "count": invoices.count(),
#                 "data": invoices_agency
#             }, status=200)

#         except Exception as e:
#             return Response({
#                 "status": 0,
#                 "message": "Something went wrong while fetching invoices.",
#                 "error": str(e),
#                 "data": None
#             }, status=200)

class ListInvoicesAPI(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        security=[{"Bearer": []}],
        operation_summary="List Invoices (Agency, Vehicle Owner, Rider)",
        operation_description=(
            "Returns invoices based on logged-in user type:\n"
            "- Lease Agency → All invoices for orders under the agency\n"
            "- Vehicle Owner → All invoices for vehicles owned by the owner\n"
            "- Rider → All invoices for user's own orders\n"
            "Automatically detects role from `request.user.user_type`."
        ),
    )
    def get(self, request):
        try:
            user = request.user
            user_type = user.user_type.user_type_name
            auto_cancel_timeout_orders()
            auto_update_scheduled_orders_status()

            # ==========================
            # CASE 1: LEASE AGENCY
            # ==========================
            if user_type == "LeaseAgency":
                try:
                    agency = Lease_Agency_Master.objects.get(user_id=user)
                except Lease_Agency_Master.DoesNotExist:
                    return Response({"status": 0, "message": "Invalid Lease Agency"}, status=200)

                invoices = LeaseInvoice.objects.filter(
                    lease_order__agency=agency
                ).order_by("-created_at")

            # ==========================
            # CASE 2: VEHICLE OWNER
            # ==========================
            elif user_type == "Owner":
                try:
                    owner = Vehicle_Owner_Master.objects.get(user_id=user)
                except Vehicle_Owner_Master.DoesNotExist:
                    return Response({"status": 0, "message": "Invalid Vehicle Owner"}, status=200)

                invoices = LeaseInvoice.objects.filter(
                    lease_order__vehicle__vehicle_owner=owner
                ).order_by("-created_at")

            # ==========================
            # ==========================
            elif user_type == "Rider":
                invoices = LeaseInvoice.objects.filter(
                    lease_order__user=user
                ).order_by("-created_at")                
            else:
                return Response({"status": 0, "message": "Invalid User Type"}, status=200)

            # BUILD RESPONSE
            invoice_list = []
            for invoice in invoices:
                vehicle_image = invoice.lease_order.vehicle.images.first()
                vehicle_image_url = (
                    request.build_absolute_uri(vehicle_image.image.url)
                    if vehicle_image else None
                )

                invoice_list.append({
                    "invoice_id": str(invoice.invoice_id),
                    "invoice_number": invoice.invoice_number,
                    "invoice_status": invoice.invoice_status.invoice_status_name,
                    "micro_insurance": str(invoice.micro_insurance),
                    "subtotal": str(invoice.subtotal),
                    "vat": str(invoice.vat),
                    "total_amount": str(invoice.total_amount),
                    "discount": str(invoice.discount),
                    "delivery_cost": str(invoice.delivery_cost),
                    "note": invoice.note,
                    "due_date": str(invoice.due_date),
                    "created_at": str(invoice.created_at),
                    "invoice_pdf": request.build_absolute_uri(invoice.invoice_pdf.url) if invoice.invoice_pdf else None,

                    # ORDER & USER
                    "order_number": invoice.lease_order.order_number,
                    "start_date": invoice.lease_order.start_date,
                    "end_date": invoice.lease_order.end_date,
                    "total_days": invoice.lease_order.total_days,
                    "customer_name": f"{invoice.lease_order.user.first_name} {invoice.lease_order.user.last_name}".strip(),

                    # VEHICLE
                    "vehicle_make": invoice.lease_order.vehicle.vehicle_make,
                    "vehicle_model": invoice.lease_order.vehicle.vehicle_model,
                    "plate_number": invoice.lease_order.vehicle.plate_number,
                    "mfg_year": invoice.lease_order.vehicle.mfg_year,
                    "lease_price_per_day": invoice.lease_order.vehicle.lease_price_per_day,
                    "vehicle_image": vehicle_image_url,

                    # AGENCY
                    "agency_name": invoice.lease_order.agency.business_name,
                    "agency_phone": invoice.lease_order.agency.phone_number,
                    "agency_profile": request.build_absolute_uri(invoice.lease_order.agency.agency_profile.url)
                                        if invoice.lease_order.agency.agency_profile else None,
                })

            return Response({
                "status": 1,
                "message": "Invoices fetched successfully",
                "count": len(invoice_list),
                "data": invoice_list
            }, status=200)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong.",
                "error": str(e)
            }, status=200)


class SendInvoiceAPI(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Send Invoice (Lease Agency Only)",
        operation_description=(
            "Allows a Lease Agency to send an invoice.\n"
            "- Invoice status → Pending\n"
            "- Order status → invoice_processing\n"
            "- Only the owning agency can send the invoice\n"
            "- invoice_id must be valid"
        ),
        security=[{"Bearer": []}],
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["invoice_id"],
            properties={
                "invoice_id": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="UUID of the invoice"
                )
            }
        ),
        responses={
            200: openapi.Response(
                description="Invoice sent successfully"
            )
        }
    )
    def post(self, request):
        try:
            invoice_id = request.data.get("invoice_id")

            if not invoice_id:
                return Response({
                    "status": 0,
                    "message": "invoice_id is required.",
                    "error": "Missing invoice_id",
                    "data": None
                }, status=200)

            # Validate lease agency
            try:
                agency = Lease_Agency_Master.objects.get(user_id=request.user)
            except Lease_Agency_Master.DoesNotExist:
                return Response({
                    "status": 0,
                    "message": "Only Lease Agencies can send invoices.",
                    "error": "Permission denied",
                    "data": None
                }, status=200)

            # Fetch invoice
            try:
                invoice = LeaseInvoice.objects.select_related("lease_order").get(invoice_id=invoice_id)
            except LeaseInvoice.DoesNotExist:
                return Response({
                    "status": 0,
                    "message": "Invoice not found.",
                    "error": "Invalid invoice_id",
                    "data": None
                }, status=200)

            order = invoice.lease_order

            # Ensure invoice belongs to agency
            if order.agency.id != agency.id:
                return Response({
                    "status": 0,
                    "message": "You are not authorized to send this invoice.",
                    "error": "Unauthorized access",
                    "data": None
                }, status=200)

            # Get invoice status = Pending
            try:
                invoice_status = InvoiceStatusMaster.objects.get(
                    invoice_status_name__iexact="Pending"
                )
            except InvoiceStatusMaster.DoesNotExist:
                return Response({
                    "status": 0,
                    "message": "Invoice status 'Pending' is not configured.",
                    "error": "Missing master status",
                    "data": None
                }, status=200)

            invoice.invoice_status = invoice_status
            invoice.save()

            # Get order status = Processing
            try:
                order_status = OrderStatusMaster.objects.get(
                    order_status_name__iexact="invoice_processing"
                )
            except OrderStatusMaster.DoesNotExist:
                return Response({
                    "status": 0,
                    "message": "Order status 'invoice_processing' is not configured.",
                    "error": "Missing master status",
                    "data": None
                }, status=200)

            order.order_status = order_status
            order.save()

            # formatted = locale.format_string("%d", invoice.total_amount, grouping=True)
            formatted = f"{invoice.total_amount:,.0f}"
            # Success response
            send_email(email_type="invoice_ready",to_email=order.user.email,context={"invoice":invoice,"total_amount": formatted})
            send_email(email_type="invoice_template",to_email=order.user.email,context={"invoice":invoice,"total_amount": formatted})

            return Response({
                "status": 1,
                "message": "Invoice sent successfully.",
                "invoice_number": invoice.invoice_number,
                "order_number": order.order_number,
                "new_invoice_status": invoice_status.invoice_status_name,
                "new_order_status": order_status.order_status_name
            }, status=200)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong while sending the invoice.",
                "error": str(e),
                "data": None
            }, status=200)


class OwnerScheduleLeaseOrderAPI(APIView):
    permission_classes = [IsAuthenticated]
    @swagger_auto_schema(
        security=[{'Bearer': []}],
        operation_summary="Scheduled or Cancel Order (Owner)",
        operation_description=(
            "Allows a vehicle owner to move an order from 'booking' → 'scheduled' or 'cancelled'. "
            "Other transitions are not allowed."
        ),
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["order_id", "new_status"],
            properties={
                "order_id": openapi.Schema(type=openapi.TYPE_STRING, description="Lease Order ID (UUID)"),
                "new_status": openapi.Schema(type=openapi.TYPE_STRING, description="New Status Name — must be scheduled or cancelled")
            },
            example={"order_id": "uuid", "new_status": "uuid"}
        ),
        responses={
            1: openapi.Response(
                description="Order scheduled or cancelled successfully",
                examples={
                    "application/json": {
                        "status": 1,
                        "message": "Order scheduled successfully by vehicle owner.",
                        "data": {
                            "order_id": "uuid",
                            "previous_status": "in_review",
                            "new_status_id": "uuid",
                            "new_status_name": "confirm",
                            "updated_at": "2025-10-30T10:00:00Z"
                        }
                    }
                }
            ),
            0: openapi.Response(
                description="Invalid transition or missing fields",
                examples={"application/json": {"status": 0, "message": "Invalid transition. Owner can only move to 'scheduled' or 'cancelled'."}}
            ),
            0: openapi.Response(
                description="Order or Status not found",
                examples={"application/json": {"status": 0, "message": "Lease order not found for the given order_id."}}
            ),
            0: openapi.Response(
                description="Internal Server Error",
                examples={"application/json": {"status": 0, "message": "Something went wrong while updating order status (Owner)."}}
            ),
        }
    )
    def patch(self, request):
        try:
            order_id = request.data.get("order_id")
            new_status = request.data.get("new_status")

            if not order_id or not new_status:
                return Response({"status": 0, "message": "Both 'order_id' and 'new_status' are required.","data":None}, status=200)

            lease_order = LeaseOrderMaster.objects.select_related("order_status").filter(lease_order_id=order_id).first()
            if not lease_order:
                return Response({"status": 0, "message": "Lease order not found for the given order_id.","data":None}, status=200)

            current_status = lease_order.order_status.order_status_name.lower()
            if current_status != "invoice_paid":
                return Response({"status": 0, "message": f"Order cannot be updated from '{current_status}' by owner.","data":None}, status=200)

       
            try:
                new_status_obj = OrderStatusMaster.objects.get(order_status_name__iexact=new_status)
            except OrderStatusMaster.DoesNotExist:
                 return Response({"status": 0, "message": "Invalid status name", "data": None}, status=200)
           
            new_status_name = new_status_obj.order_status_name.lower()
            if new_status_name not in ["scheduled", "cancelled"]:
                return Response({"status": 0, "message": "Invalid transition. Owner can only move to 'scheduled' or 'cancelled'.","data":None}, status=200)

            lease_order.order_status = new_status_obj
            lease_order.updated_at = datetime.now()
            lease_order.save()

            if new_status_name == "scheduled":
                message_note = "Order scheduled successfully by vehicle owner."
            elif new_status_name == "cancelled":
                message_note = "Order cancelled successfully by vehicle owner."

            return Response({
                "status": 1,
                "message": message_note,
                "data": {
                    "order_id": str(lease_order.lease_order_id),
                    "previous_status": current_status,
                    "new_status_id": str(new_status_obj.id),
                    "new_status_name": new_status_obj.order_status_name,
                    "updated_at": lease_order.updated_at,
                }
            }, status=200)

        except Exception as e:
            return Response({"status": 0, "message": "Something went wrong while updating order status (Owner).", "error": str(e)}, status=200)

class GetInvoiceAPI(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Get Invoice by Order ID",
        operation_description=(
            "Fetch invoice using order_id and invoice_type. "
            "Returns FULL invoice table data."
        ),
        manual_parameters=[
            openapi.Parameter(
                "order_id",
                openapi.IN_QUERY,
                description="Lease Order UUID",
                type=openapi.TYPE_STRING,
                required=True,
            ),
            openapi.Parameter(
                "invoice_type",
                openapi.IN_QUERY,
                description="Invoice Status (Created, Pending, Completed etc.)",
                type=openapi.TYPE_STRING,
                required=True,
            ),
        ],
        responses={200: "Invoice fetched successfully"},
    )
    def get(self, request):
        try:
            order_id = request.query_params.get("order_id")
            invoice_type = request.query_params.get("invoice_type")

            if not order_id or not invoice_type:
                return Response({
                    "status": 0,
                    "message": "order_id and invoice_type are required",
                    "error": "Missing parameters",
                    "data": None
                }, status=200)

            # Validate order
            try:
                order = LeaseOrderMaster.objects.select_related("user","agency","vehicle").get(
                    lease_order_id=order_id
                )
            except LeaseOrderMaster.DoesNotExist:
                return Response({
                    "status": 0,
                    "message": "Order not found",
                    "error": "Invalid order_id",
                    "data": None
                }, status=200)

            # Validate invoice status
            try:
                status_obj = InvoiceStatusMaster.objects.get(
                    invoice_status_name=invoice_type
                )
            except InvoiceStatusMaster.DoesNotExist:
                return Response({
                    "status": 0,
                    "message": "Invalid invoice_type",
                    "error": "Status not found",
                    "data": None
                }, status=200)

            # Fetch invoice for the given order and invoice type
            invoice = LeaseInvoice.objects.filter(
                lease_order=order, invoice_status=status_obj
            ).first()

            if not invoice:
                return Response({
                    "status": 0,
                    "message": "No invoice found for the given type",
                    "error": "Invoice missing",
                    "data": None
                }, status=200)

            vehicle_image = order.vehicle.images.first()
            vehicle_image_url = (
                request.build_absolute_uri(vehicle_image.image.url)
                if vehicle_image else None
            )
            # Prepare full response data
            data = {
                "invoice_id": str(invoice.invoice_id),
                "invoice_number": invoice.invoice_number,
                "invoice_status": invoice.invoice_status.invoice_status_name,
                "micro_insurance": str(invoice.micro_insurance),
                "subtotal": str(invoice.subtotal),
                "delivery_cost": str(invoice.delivery_cost),
                "discount": str(invoice.discount),
                "vat": str(invoice.vat),
                "total_amount": str(invoice.total_amount),
                "note": invoice.note,
                "invoice_pdf": request.build_absolute_uri(invoice.invoice_pdf.url) if invoice.invoice_pdf else None,
                "due_date": str(invoice.due_date),
                "created_at": str(invoice.created_at),

                # Related data
                "customer_name": f"{order.user.first_name} {order.user.last_name}".strip(),
                "order_number": order.order_number,
                "total_days":order.total_days,
                "vehicle_make":order.vehicle.vehicle_make,
                "vehicle_model":order.vehicle.vehicle_model,
                "vin":order.vehicle.vehicle_identify_number,
                "mfg_year":order.vehicle.mfg_year,
                "lease_per_day":order.vehicle.lease_price_per_day,
                "vehicle_total_amount": order.vehicle.lease_price_per_day * order.total_days,
                "agency_profile":request.build_absolute_uri(order.agency.agency_profile.url) if getattr(order.agency, "agency_profile", None) else None,
                "business_name":order.agency.business_name,
                "vehicle_image_url": vehicle_image_url,
                "agency_phone_number": order.agency.phone_number,
                "vehicle_plate_number": order.vehicle.plate_number
            }

            return Response({
                "status": 1,
                "message": "Invoice fetched successfully",
                "data": data
            }, status=200)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong while fetching invoice",
                "error": str(e),
                "data": None
            }, status=200)

class CreatePaymentAPI(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        security=[{'Bearer': []}],
        operation_summary="Create Payment (Rider Only)",
        operation_description=(
            "Allows a **Rider** to make a payment for a specific invoice. "
            "Supports `invoice_type`: `invoice_paid`, `rider_declined`. \n"
            "- If `invoice_type = invoice_paid` → Requires payment success. \n"
            "- If `invoice_type = rider_declined` → Only order status updates.\n"
        ),
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=[
                "transactionId", "trxref", "invoiceId",
                "amount", "payment_method", "payment_status",
                "invoice_type"
            ],
            properties={
                "transactionId": openapi.Schema(type=openapi.TYPE_STRING),
                "trxref": openapi.Schema(type=openapi.TYPE_STRING),
                "invoiceId": openapi.Schema(type=openapi.TYPE_STRING),
                "amount": openapi.Schema(type=openapi.TYPE_NUMBER),
                "payment_method": openapi.Schema(type=openapi.TYPE_STRING),
                "payment_status": openapi.Schema(type=openapi.TYPE_STRING),
                "invoice_type": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="Valid: invoice_paid, rider_declined"
                ),
            },
            example={
                "transactionId": "TRX123456789",
                "trxref": "PAYREF987654",
                "invoiceId": "UUID-of-invoice",
                "amount": 1500.00,
                "payment_method": "Card",
                "payment_status": "success",
                "invoice_type": "invoice_paid"
            }
        )
    )

    def post(self, request):
        try:
            # Ensure user is Rider
            if request.user.user_type.user_type_name != "Rider":
                return Response({
                    "status": 0,
                    "message": "Only Riders can make payments.",
                    "error": "Permission denied",
                    "data": None
                }, status=200)

            data = request.data

            allowed_invoice_types = ["invoice_paid", "rider_declined"]
            if data["invoice_type"] not in allowed_invoice_types:
                return Response({
                    "status": 0,
                    "message": "Invalid invoice_type",
                    "error": f"Allowed values: {allowed_invoice_types}",
                    "data": None
                }, status=200)

            # Get Invoice
            try:
                invoice = LeaseInvoice.objects.get(invoice_id=data["invoiceId"])
            except LeaseInvoice.DoesNotExist:
                return Response({
                    "status": 0,
                    "message": "Invalid invoiceId",
                    "error": "Invoice not found",
                    "data": None
                }, status=200)

            lease_order = invoice.lease_order
            # Ensure invoice belongs to this Rider
            if invoice.lease_order.user.id != request.user.id:
                return Response({
                    "status": 0,
                    "message": "This invoice does not belong to you.",
                    "error": "Unauthorized access",
                    "data": None
                }, status=200)

            if data["invoice_type"] == "rider_declined":
                # Invoice stays "Created"
                created_state = InvoiceStatusMaster.objects.get(invoice_status_name="Created")
                invoice.invoice_status = created_state
                invoice.save()

                # Order → rider_declined
                declined_status = OrderStatusMaster.objects.get(order_status_name="rider_declined")
                lease_order.order_status = declined_status
                lease_order.save()

                return Response({
                    "status": 1,
                    "message": "Invoice declined by rider",
                    "data": None
                }, status=200)

            # Required fields validation
            required = [
                "transactionId", "trxref", "invoiceId",
                "amount", "payment_method", "payment_status",
                "invoice_type"
            ]
            missing = [f for f in required if f not in data or data[f] == ""]
            if missing:
                return Response({
                    "status": 0,
                    "message": "Missing required fields",
                    "error": {f: "This field is required" for f in missing},
                    "data": None
                }, status=200)

            
            # Prevent duplicate successful payment
            existing_payment = PaymentMaster.objects.filter(
                invoice=invoice,
                payment_status__payment_status_name__iexact="success"
            ).first()

            if existing_payment:
                return Response({
                    "status": 0,
                    "message": "Payment for this invoice has already been made.",
                    "error": "Duplicate payment",
                }, status=200)

            # Get Payment Method
            try:
                payment_method = PaymentMethodMaster.objects.get(
                    payment_method_name=data["payment_method"]
                )
            except PaymentMethodMaster.DoesNotExist:
                return Response({
                    "status": 0,
                    "message": "Invalid payment method",
                    "error": "Method not found",
                    "data": None
                }, status=200)

            # Get Payment Status
            try:
                payment_status = PaymentStatusMaster.objects.get(
                    payment_status_name=data["payment_status"]
                )
            except PaymentStatusMaster.DoesNotExist:
                return Response({
                    "status": 0,
                    "message": "Invalid payment status",
                    "error": "Status not found",
                    "data": None
                }, status=200)

            # Create Payment
            payment = PaymentMaster.objects.create(
                invoice=invoice,
                payment_method=payment_method,
                payment_status=payment_status,
                payment_ref=data["trxref"],
                transaction_id=data["transactionId"],
                amount=data["amount"],
                paid_at=timezone.now()
                if data["payment_status"].lower() == "success" else None
            )

            # ===============================
            #   LOGIC BASED ON invoice_type
            # ===============================

            if data["invoice_type"] == "invoice_paid":
                # Only proceed if payment success
                if data["payment_status"].lower() == "success":
                    # Update Invoice to Paid
                    paid_status = InvoiceStatusMaster.objects.get(invoice_status_name="Paid")
                    invoice.invoice_status = paid_status
                    invoice.save()

                    # Update Order to invoice_paid
                    order_paid_status = OrderStatusMaster.objects.get(order_status_name="invoice_paid")
                    lease_order.order_status = order_paid_status
                    lease_order.save()

                    payment.paid_at = timezone.now()
                    payment.save()
                    
                    pdf_url = generate_invoice_pdf(request,invoice)

            return Response({
                "status": 1,
                "message": "Payment created successfully",
                "data": PaymentSerializer(payment).data,
            }, status=200)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Error processing payment",
                "error": str(e),
                "data": None
            }, status=200)


# class CreatePaymentAPI(APIView):
#     permission_classes = [IsAuthenticated]

#     @swagger_auto_schema(
#         security=[{'Bearer': []}],
#         operation_summary="Create Payment (Rider Only)",
#         operation_description=(
#             "Allows a **Rider** to make a payment for a specific invoice. "
#             "If `payment_status` is 'success', it updates the invoice status to 'Completed' "
#             "and the order status to 'invoice_paid'."
#         ),
#         request_body=openapi.Schema(
#             type=openapi.TYPE_OBJECT,
#             required=[
#                 "transactionId", "trxref", "invoiceId", 
#                 "amount", "payment_method", "payment_status"
#             ],
#             properties={
#                 "transactionId": openapi.Schema(type=openapi.TYPE_STRING, description="Unique transaction ID"),
#                 "trxref": openapi.Schema(type=openapi.TYPE_STRING, description="Payment reference"),
#                 "invoiceId": openapi.Schema(type=openapi.TYPE_STRING, description="Invoice UUID"),
#                 "amount": openapi.Schema(type=openapi.TYPE_NUMBER, description="Payment amount"),
#                 "payment_method": openapi.Schema(type=openapi.TYPE_STRING, description="Payment method name"),
#                 "payment_status": openapi.Schema(type=openapi.TYPE_STRING, description="Payment status, e.g., 'success'")
#             },
#             example={
#                 "transactionId": "TRX123456789",
#                 "trxref": "PAYREF987654",
#                 "invoiceId": "UUID-of-invoice",
#                 "amount": 1500.00,
#                 "payment_method": "Card",
#                 "payment_status": "success"
#             }
#         ),
#         responses={
#             200: openapi.Response(
#                 description="Payment creation response",
#                 examples={
#                     "application/json": {
#                         "status": 1,
#                         "message": "Payment created successfully",
#                         "data": {
#                             "payment_id": "UUID",
#                             "invoice": "UUID-of-invoice",
#                             "payment_method": "Card",
#                             "payment_status": "success",
#                             "payment_ref": "PAYREF987654",
#                             "transaction_id": "TRX123456789",
#                             "amount": 1500.00,
#                             "paid_at": "2025-11-16T12:30:45.123Z"
#                         }
#                     },
#                     "application/json_errors": [
#                         {
#                             "status": 0,
#                             "message": "Missing required fields",
#                             "error": {"amount": "This field is required"},
#                             "data": None
#                         },
#                         {
#                             "status": 0,
#                             "message": "Invalid invoiceId",
#                             "error": "Invoice not found",
#                             "data": None
#                         },
#                         {
#                             "status": 0,
#                             "message": "This invoice does not belong to you.",
#                             "error": "Unauthorized access",
#                             "data": None
#                         },
#                         {
#                             "status": 0,
#                             "message": "Invalid payment method",
#                             "error": "Method not found",
#                             "data": None
#                         },
#                         {
#                             "status": 0,
#                             "message": "Invalid payment status",
#                             "error": "Status not found",
#                             "data": None
#                         },
#                         {
#                             "status": 0,
#                             "message": "Only Riders can make payments.",
#                             "error": "Permission denied",
#                             "data": None
#                         },
#                         {
#                             "status": 0,
#                             "message": "Error processing payment",
#                             "error": "Detailed exception message",
#                             "data": None
#                         }
#                     ]
#                 }
#             )
#         }
#     )
#     def post(self, request):
#         try:
#             # Ensure user is Rider
#             if request.user.user_type.user_type_name != "Rider":
#                 return Response({
#                     "status": 0,
#                     "message": "Only Riders can make payments.",
#                     "error": "Permission denied",
#                     "data": None
#                 }, status=200)

#             data = request.data

#             # Required fields validation
#             required_fields = [
#                 "transactionId", "trxref", "invoiceId",
#                 "amount", "payment_method", "payment_status"
#             ]
#             missing_fields = [f for f in required_fields if f not in data or data[f] == ""]
#             if missing_fields:
#                 return Response({
#                     "status": 0,
#                     "message": "Missing required fields",
#                     "error": {f: "This field is required" for f in missing_fields},
#                     "data": None
#                 }, status=200)

#             # Get Invoice
#             try:
#                 invoice = LeaseInvoice.objects.get(invoice_id=data["invoiceId"])
#             except LeaseInvoice.DoesNotExist:
#                 return Response({
#                     "status": 0,
#                     "message": "Invalid invoiceId",
#                     "error": "Invoice not found",
#                     "data": None
#                 }, status=200)

#             # Ensure invoice belongs to this Rider
#             if invoice.lease_order.user.id != request.user.id:
#                 return Response({
#                     "status": 0,
#                     "message": "This invoice does not belong to you.",
#                     "error": "Unauthorized access",
#                     "data": None
#                 }, status=200)

#             # Get Payment Method
#             existing_payment = PaymentMaster.objects.filter(
#                 invoice=invoice,
#                 payment_status__payment_status_name__iexact="success"
#             ).first()

#             if existing_payment:
#                 return Response({
#                     "status": 0,
#                     "message": "Payment for this invoice has already been made.",
#                     "error": "Duplicate payment",
#                 }, status=200)
#             try:
#                 payment_method = PaymentMethodMaster.objects.get(
#                     payment_method_name=data["payment_method"]
#                 )
#             except PaymentMethodMaster.DoesNotExist:
#                 return Response({
#                     "status": 0,
#                     "message": "Invalid payment method",
#                     "error": "Method not found",
#                     "data": None
#                 }, status=200)

#             # Get Payment Status
#             try:
#                 payment_status = PaymentStatusMaster.objects.get(
#                     payment_status_name=data["payment_status"]
#                 )
#             except PaymentStatusMaster.DoesNotExist:
#                 return Response({
#                     "status": 0,
#                     "message": "Invalid payment status",
#                     "error": "Status not found",
#                     "data": None
#                 }, status=200)

#             # Create Payment
#             payment = PaymentMaster.objects.create(
#                 invoice=invoice,
#                 payment_method=payment_method,
#                 payment_status=payment_status,
#                 payment_ref=data["trxref"],
#                 transaction_id=data["transactionId"],
#                 amount=data["amount"],
#                 paid_at=timezone.now() if data["payment_status"].lower() == "success" else None
#             )

#             # Update Invoice & Order Status if payment successful
#             if data["payment_status"].lower() == "success":
#                 try:
#                     completed_invoice_status = InvoiceStatusMaster.objects.get(
#                         invoice_status_name="Paid"
#                     )
#                     invoice.invoice_status = completed_invoice_status
#                     invoice.save()
#                 except InvoiceStatusMaster.DoesNotExist:
#                     return Response({
#                         "status": 0,
#                         "message": "Completed invoice status not found.",
#                         "error": "Master missing",
#                         "data": None
#                     }, status=200)

#                 try:
#                     confirmed_order_status = OrderStatusMaster.objects.get(
#                         order_status_name="invoice_paid"
#                     )
#                     lease_order = invoice.lease_order
#                     lease_order.order_status = confirmed_order_status
#                     lease_order.save()
#                 except OrderStatusMaster.DoesNotExist:
#                     return Response({
#                         "status": 0,
#                         "message": "Confirmed order status not found.",
#                         "error": "Master missing",
#                         "data": None
#                     }, status=200)

#                 payment.paid_at = timezone.now()
#                 payment.save()

#             return Response({
#                 "status": 1,
#                 "message": "Payment created successfully",
#                 "data": PaymentSerializer(payment).data
#             }, status=200)

#         except Exception as e:
#             return Response({
#                 "status": 0,
#                 "message": "Error processing payment",
#                 "error": str(e),
#                 "data": None
#             }, status=200)


class UpdateInvoiceAPI(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        security=[{'Bearer': []}],
        operation_summary="Update Invoice (Lease Agency Only)",
        operation_description=(
            "Updates an existing invoice.\n\n"
            "⚠ IMPORTANT LOGIC:\n"
            "- `invoice_status` is NOT accepted from request.\n"
            "- Invoice status is always updated to **Pending** automatically.\n"
            "- Order status is updated to **Invoiced** automatically.\n"
            "- VAT (7.5%) & Total are recalculated if subtotal changes."
        ),
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["invoice_id"],
            properties={
                "invoice_id": openapi.Schema(type=openapi.TYPE_STRING, description="UUID of the invoice"),
                "micro_insurance": openapi.Schema(type=openapi.TYPE_STRING),
                "subtotal": openapi.Schema(type=openapi.TYPE_STRING),
                "delivery_cost": openapi.Schema(type=openapi.TYPE_STRING),
                "discount": openapi.Schema(type=openapi.TYPE_STRING),
                "note": openapi.Schema(type=openapi.TYPE_STRING),
                "due_date": openapi.Schema(type=openapi.TYPE_STRING, format="date")
            },
            example={
                "invoice_id": "ba6c4f34-5d23-4bd4-a951-aa9281f398ab",
                "micro_insurance": "200.00",
                "subtotal": "1500.00",
                "delivery_cost": "100.00",
                "discount": "50.00",
                "note": "Updated invoice",
                "due_date": "2025-11-30"
            }
        )
    )
    def patch(self, request):
        try:
            data = request.data.copy()
            invoice_id = data.get("invoice_id")
            if not invoice_id:
                return Response({
                    "status": 0,
                    "message": "invoice_id is required."
                }, status=200)

            # Validate agency user
            try:
                agency = Lease_Agency_Master.objects.get(user_id=request.user)
            except Lease_Agency_Master.DoesNotExist:
                return Response({
                    "status": 0,
                    "message": "Only Lease Agencies can update invoices."
                }, status=200)

            # Fetch invoice
            try:
                invoice = LeaseInvoice.objects.get(invoice_id=invoice_id)
            except LeaseInvoice.DoesNotExist:
                return Response({
                    "status": 0,
                    "message": "Invoice not found."
                }, status=200)

            # Ownership check
            if invoice.lease_order.agency != agency:
                return Response({
                    "status": 0,
                    "message": "Not authorized to update this invoice."
                }, status=200)

            # Remove invoice_status if frontend sends it
            data.pop("invoice_status", None)

            # Force invoice_status = Pending
            try:
                pending_status = InvoiceStatusMaster.objects.get(invoice_status_name="Created")
                data["invoice_status"] = str(pending_status.invoice_status_id)
            except InvoiceStatusMaster.DoesNotExist:
                return Response({
                    "status": 0,
                    "message": "Invoice status 'Pending' not found."
                }, status=200)

            # Recalculate VAT + total if subtotal changes
            if "subtotal" in data:
                try:
                    subtotal = Decimal(data.get("subtotal"))
                    vat = subtotal * Decimal("0.075")
                    total_amount = subtotal + vat
                    data["vat"] = round(vat, 2)
                    data["total_amount"] = round(total_amount, 2)
                except Exception:
                    return Response({
                        "status": 0,
                        "message": "Invalid subtotal value."
                    }, status=200)

            # Save invoice updates
            serializer = LeaseInvoiceSerializer(invoice, data=data, partial=True)

            if serializer.is_valid():
                updated_invoice = serializer.save()

                # ===== Update Order Status to Invoiced =====
                try:
                    invoiced_status = OrderStatusMaster.objects.get(order_status_name="invoiced")
                    invoice.lease_order.order_status = invoiced_status
                    invoice.lease_order.save()
                except OrderStatusMaster.DoesNotExist:
                    return Response({
                        "status": 0,
                        "message": "Order status 'invoiced' not found."
                    }, status=200)

                return Response({
                    "status": 1,
                    "message": "Invoice updated successfully",
                    "data": LeaseInvoiceSerializer(updated_invoice).data
                }, status=200)

            return Response({
                "status": 0,
                "message": "Validation failed",
                "error": serializer.errors
            }, status=200)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong.",
                "error": str(e)
            }, status=200)

# class UpdateInvoiceAPI(APIView):
#     permission_classes = [IsAuthenticated]

#     @swagger_auto_schema(
#         security=[{'Bearer': []}],
#         operation_summary="Update Invoice (Lease Agency Only)",
#         operation_description=(
#             "Updates an existing invoice.\n\n"
#             "⚠ IMPORTANT LOGIC:\n"
#             "- `invoice_status` is NOT accepted from request.\n"
#             "- Invoice status is always updated to **Pending** automatically.\n"
#             "- Order status is updated to **Invoiced** automatically.\n"
#             "- VAT (7.5%) & Total are recalculated if subtotal changes."
#         ),
#         manual_parameters=[
#             openapi.Parameter(
#                 "invoice_id",
#                 openapi.IN_PATH,
#                 description="UUID of the invoice",
#                 type=openapi.TYPE_STRING,
#                 required=True
#             )
#         ],
#         request_body=openapi.Schema(
#             type=openapi.TYPE_OBJECT,
#             properties={
#                 "micro_insurance": openapi.Schema(type=openapi.TYPE_STRING),
#                 "subtotal": openapi.Schema(type=openapi.TYPE_STRING),
#                 "delivery_cost": openapi.Schema(type=openapi.TYPE_STRING),
#                 "discount": openapi.Schema(type=openapi.TYPE_STRING),
#                 "note": openapi.Schema(type=openapi.TYPE_STRING),
#                 "due_date": openapi.Schema(type=openapi.TYPE_STRING, format="date")
#             },
#             example={
#                 "micro_insurance": "200.00",
#                 "subtotal": "1500.00",
#                 "delivery_cost": "100.00",
#                 "discount": "50.00",
#                 "note": "Updated invoice",
#                 "due_date": "2025-11-30"
#             }
#         )
#     )
#     def patch(self, request, invoice_id):
#         try:
#             # Validate agency user
#             try:
#                 agency = Lease_Agency_Master.objects.get(user_id=request.user)
#             except Lease_Agency_Master.DoesNotExist:
#                 return Response({
#                     "status": 0,
#                     "message": "Only Lease Agencies can update invoices."
#                 }, status=200)

#             # Fetch invoice
#             try:
#                 invoice = LeaseInvoice.objects.get(invoice_id=invoice_id)
#             except LeaseInvoice.DoesNotExist:
#                 return Response({
#                     "status": 0,
#                     "message": "Invoice not found."
#                 }, status=200)

#             # Ownership check
#             if invoice.lease_order.agency != agency:
#                 return Response({
#                     "status": 0,
#                     "message": "Not authorized to update this invoice."
#                 }, status=200)

#             data = request.data.copy()

#             # Remove invoice_status if frontend sends it
#             data.pop("invoice_status", None)

#             # Force invoice_status = Pending
#             try:
#                 pending_status = InvoiceStatusMaster.objects.get(invoice_status_name="Pending")
#                 data["invoice_status"] = str(pending_status.invoice_status_id)
#             except InvoiceStatusMaster.DoesNotExist:
#                 return Response({
#                     "status": 0,
#                     "message": "Invoice status 'Pending' not found."
#                 }, status=200)

#             # Recalculate VAT + total if subtotal changes
#             if "subtotal" in data:
#                 from decimal import Decimal

#                 subtotal = Decimal(data.get("subtotal"))
#                 vat = subtotal * Decimal("0.075")
#                 total_amount = subtotal + vat

#                 data["vat"] = round(vat, 2)
#                 data["total_amount"] = round(total_amount, 2)

#             # Save invoice updates
#             serializer = LeaseInvoiceSerializer(invoice, data=data, partial=True)

#             if serializer.is_valid():
#                 updated_invoice = serializer.save()

#                 # ===== Update Order Status to Invoiced =====
#                 try:
#                     invoiced_status = OrderStatusMaster.objects.get(order_status_name="invoiced")
#                     invoice.lease_order.order_status = invoiced_status
#                     invoice.lease_order.save()
#                 except OrderStatusMaster.DoesNotExist:
#                     return Response({
#                         "status": 0,
#                         "message": "Order status 'invoiced' not found."
#                     }, status=200)

#                 return Response({
#                     "status": 1,
#                     "message": "Invoice updated successfully",
#                     "data": LeaseInvoiceSerializer(updated_invoice).data
#                 }, status=200)

#             return Response({
#                 "status": 0,
#                 "message": "Validation failed",
#                 "error": serializer.errors
#             }, status=200)

#         except Exception as e:
#             return Response({
#                 "status": 0,
#                 "message": "Something went wrong.",
#                 "error": str(e)
#             }, status=200)

class AgencyScheduleLeaseOrderAPI(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        security=[{'Bearer': []}],
        operation_summary="Schedule(Lease Agency)",
        operation_description=(
            "Allows a **Lease Agency** to move an order from 'invoice_paid' → 'scheduled'. "
            "Other transitions are not allowed."
        ),
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["order_id", "new_status"],
            properties={
                "order_id": openapi.Schema(type=openapi.TYPE_STRING, description="Lease Order ID (UUID)"),
                "new_status": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="New Status Name — must be 'scheduled'"
                ),
            },
            example={"order_id": "uuid", "new_status": "scheduled"}
        ),
        responses={
            200: openapi.Response(
                description="Order scheduled successfully",
                examples={
                    "application/json": {
                        "status": 1,
                        "message": "Order scheduled successfully by lease agency.",
                        "data": {
                            "order_id": "uuid",
                            "previous_status": "invoice_paid",
                            "new_status_id": "uuid",
                            "new_status_name": "scheduled",
                            "updated_at": "2025-10-30T10:00:00Z"
                        }
                    }
                }
            ),
            400: openapi.Response(
                description="Invalid Transition",
                examples={"application/json": {"status": 0, "message": "Invalid transition. Agency can only move to 'scheduled' or 'cancelled'."}}
            ),
            404: openapi.Response(
                description="Order Not Found",
                examples={"application/json": {"status": 0, "message": "Lease order not found for the given order_id."}}
            ),
            500: openapi.Response(
                description="Internal Server Error",
                examples={"application/json": {"status": 0, "message": "Something went wrong while updating order status (Agency)."}}
            ),
        }
    )
    def patch(self, request):
        try:
            # ---------------------------
            # Validate agency user
            # ---------------------------
            if request.user.user_type.user_type_name != "LeaseAgency":
                return Response({
                    "status": 0,
                    "message": "Only Lease Agency can update this order.",
                    "data": None
                }, status=200)

            order_id = request.data.get("order_id")
            new_status = request.data.get("new_status")

            if not order_id or not new_status:
                return Response({
                    "status": 0,
                    "message": "Both 'order_id' and 'new_status' are required.",
                    "data": None
                }, status=200)

            lease_order = LeaseOrderMaster.objects.select_related("order_status").filter(
                lease_order_id=order_id
            ).first()

            if not lease_order:
                return Response({
                    "status": 0,
                    "message": "Lease order not found for the given order_id.",
                    "data": None
                }, status=200)

            current_status = lease_order.order_status.order_status_name.lower()

            if current_status != "invoice_paid":
                return Response({
                    "status": 0,
                    "message": f"Order cannot be updated from '{current_status}' by lease agency.",
                    "data": None
                }, status=200)

            # ---------------------------
            # Validate new status
            # ---------------------------
            try:
                new_status_obj = OrderStatusMaster.objects.get(
                    order_status_name__iexact=new_status
                )
            except OrderStatusMaster.DoesNotExist:
                return Response({
                    "status": 0,
                    "message": "Invalid status name.",
                    "data": None
                }, status=200)

            new_status_name = new_status_obj.order_status_name.lower()

            if new_status_name not in ["scheduled"]:
                return Response({
                    "status": 0,
                    "message": "Invalid transition. Agency can only move to 'scheduled' or 'cancelled'.",
                    "data": None
                }, status=200)

            # ---------------------------
            # Update order
            # ---------------------------
            lease_order.order_status = new_status_obj
            lease_order.updated_at = datetime.now()
            lease_order.save()

            # ---------------------------
            # Custom messages
            # ---------------------------

            invoice = LeaseInvoice.objects.filter(lease_order=lease_order).order_by("-created_at").first()
            payment = PaymentMaster.objects.filter(invoice=invoice,payment_status__payment_status_name__iexact="success").first()
            # formatted = locale.format_string("%d", payment.amount, grouping=True)
            formatted = f"{payment.amount:,.0f}"
            send_email(email_type="payment_confirm",to_email=lease_order.user.email,context={"payment":payment,"amount": formatted})

            if new_status_name == "scheduled":
                message_note = "Order scheduled successfully by lease agency."

            return Response({
                "status": 1,
                "message": message_note,
                "data": {
                    "order_id": str(lease_order.lease_order_id),
                    "previous_status": current_status,
                    "new_status_id": str(new_status_obj.id),
                    "new_status_name": new_status_obj.order_status_name,
                    "updated_at": lease_order.updated_at,
                }
            }, status=200)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong while updating order status (Agency).",
                "error": str(e),
                "data": None
            }, status=200)

# class CancelOrderAPI(APIView):
#     permission_classes = [IsAuthenticated]

#     @swagger_auto_schema(
#         security=[{'Bearer': []}],
#         operation_summary="Cancel Lease Order (User-wise Cancellation)",
#         operation_description=(
#             "Cancel an order depending on user type — LeaseAgency, Owner, or Rider. "
#             "Automatically updates order status to 'cancelled' and stores the correct cancellation reason."
#         ),
#         request_body=openapi.Schema(
#             type=openapi.TYPE_OBJECT,
#             required=["order_id"],
#             properties={
#                 "order_id": openapi.Schema(type=openapi.TYPE_STRING),
#             },
#             example={"order_id": "uuid"}
#         )
#     )
#     def patch(self, request):
#         try:
#             order_id = request.data.get("order_id")
#             user = request.user

#             try:
#                 cancelled_invoice_status = InvoiceStatusMaster.objects.get(invoice_status_name="Cancelled")
#             except (InvoiceStatusMaster.DoesNotExist):
#                 return Response({"status": 0, "message": "Invoice Status Does Not Exits"},status=200)

#             if not order_id:
#                 return Response({"status": 0, "message": "order_id is required", "data": None}, status=200)

#             lease_order = LeaseOrderMaster.objects.filter(lease_order_id=order_id).first()
#             if not lease_order:
#                 return Response({"status": 0, "message": "Lease order not found", "data": None}, status=200)

#             # =============================
#             # Identify user type
#             # =============================
#             user_type = user.user_type.user_type_name.lower()

#             if user_type == "leaseagency":
#                 cancel_type = "Lease Agency"
#                 reason_id = "3a25017c-f02a-4eca-8430-76bb2a18446c"

#             elif user_type == "owner":
#                 # Owner must match order owner
#                 if lease_order.user != user:
#                     return Response({"status": 0, "message": "You cannot cancel someone else's order.", "data": None}, status=200)

#                 cancel_type = "Owner"
#                 reason_id = "58c4408e-dc9a-4dfd-80a9-efa95d327565"

#             elif user_type == "rider":
#                 cancel_type = "Rider"
#                 reason_id = "f7b4eddf-176e-4c30-9787-618896818ff0"

#             else:
#                 return Response({"status": 0, "message": "This user is not allowed to cancel orders.", "data": None}, status=200)

#             # =============================
#             # Get cancellation reason
#             # =============================
#             try:
#                 cancel_reason = CancellationReasonMaster.objects.get(
#                     cancellation_reason_id=reason_id
#                 )
#             except CancellationReasonMaster.DoesNotExist:
#                 return Response({"status": 0, "message": "Cancellation reason not found", "data": None}, status=200)

#             # =============================
#             # Update Order Status to Cancelled
#             # =============================
#             try:
#                 cancelled_status = OrderStatusMaster.objects.get(
#                     order_status_name__iexact="cancelled"
#                 )
#             except OrderStatusMaster.DoesNotExist:
#                 return Response({"status": 0, "message": "Cancelled status not found", "data": None}, status=200)

#             lease_order.order_status = cancelled_status
#             lease_order.cancellation_reason = cancel_reason
#             lease_order.updated_at = timezone.now()
#             lease_order.save()

#             order_invoices = LeaseInvoice.objects.filter(lease_order=lease_order).exclude(
#                 invoice_status__invoice_status_name="Cancelled"
#             )
#             for invoice in order_invoices:
#                 invoice.invoice_status = cancelled_invoice_status
#                 invoice.save()

#             return Response({
#                 "status": 1,
#                 "message": f"Order cancelled successfully by {cancel_type}.",
#                 "data": {
#                     "order_id": str(lease_order.lease_order_id),
#                     "cancelled_by": cancel_type,
#                     "cancellation_reason": cancel_reason.reason_name,
#                     "updated_at": lease_order.updated_at,
#                 }
#             }, status=200)

#         except Exception as e:
#             return Response({
#                 "status": 0,
#                 "message": "Something went wrong while cancelling lease order.",
#                 "error": str(e)
#             }, status=200)

class CancelOrderAPI(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        security=[{'Bearer': []}],
        operation_summary="Cancel Lease Order (User-wise Cancellation)",
        operation_description=(
            "Cancel an order depending on user type — LeaseAgency, Owner, or Rider. "
            "Automatically updates order status to 'cancelled' and stores the correct cancellation reason."
        ),
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["order_id"],
            properties={
                "order_id": openapi.Schema(type=openapi.TYPE_STRING),
            },
            example={"order_id": "uuid"}
        )
    )
    def patch(self, request):
        try:
            order_id = request.data.get("order_id")
            user = request.user

            # Fetch invoice cancelled status
            try:
                cancelled_invoice_status = InvoiceStatusMaster.objects.get(
                    invoice_status_name="Cancelled"
                )
            except InvoiceStatusMaster.DoesNotExist:
                return Response(
                    {"status": 0, "message": "Invoice Status Does Not Exist"},
                    status=200
                )

            if not order_id:
                return Response(
                    {"status": 0, "message": "order_id is required", "data": None},
                    status=200
                )

            lease_order = LeaseOrderMaster.objects.filter(
                lease_order_id=order_id
            ).first()
            if not lease_order:
                return Response(
                    {"status": 0, "message": "Lease order not found", "data": None},
                    status=200
                )

            # =============================
            # Identify user type → reason_name
            # =============================
            user_type = user.user_type.user_type_name.lower()

            if user_type == "leaseagency":
                cancel_type = "Lease Agency"
                reason_name = "Agency Cancelled"

            elif user_type == "owner":
                if lease_order.user != user:
                    return Response(
                        {"status": 0, "message": "You cannot cancel someone else's order.", "data": None},
                        status=200
                    )
                cancel_type = "Owner"
                reason_name = "Owner Cancelled"

            elif user_type == "rider":
                cancel_type = "Rider"
                reason_name = "Rider Cancelled"

            else:
                return Response(
                    {"status": 0, "message": "This user is not allowed to cancel orders.", "data": None},
                    status=200
                )

            # =============================
            # Fetch cancellation reason using name
            # =============================
            try:
                cancel_reason = CancellationReasonMaster.objects.get(
                    reason_name=reason_name
                )
            except CancellationReasonMaster.DoesNotExist:
                return Response(
                    {"status": 0, "message": f"Cancellation reason '{reason_name}' not found", "data": None},
                    status=200
                )

            # =============================
            # Update Order Status to Cancelled
            # =============================
            try:
                cancelled_status = OrderStatusMaster.objects.get(
                    order_status_name__iexact="cancelled"
                )
            except OrderStatusMaster.DoesNotExist:
                return Response(
                    {"status": 0, "message": "Cancelled status not found", "data": None},
                    status=200
                )

            lease_order.order_status = cancelled_status
            lease_order.cancellation_reason = cancel_reason
            lease_order.updated_at = timezone.now()
            lease_order.save()



            # idle_status = VehicleStatusMaster.objects.get(vehicle_status_name="idle")
            # vehicle = lease_order.vehicle
            # vehicle.vehicle_status = idle_status
            # vehicle.save(update_fields=["vehicle_status"])

            # Cancel all NON-cancelled invoices
            order_invoices = LeaseInvoice.objects.filter(
                lease_order=lease_order
            ).exclude(invoice_status__invoice_status_name="Cancelled")

            for invoice in order_invoices:
                invoice.invoice_status = cancelled_invoice_status
                invoice.save()

            if user_type == "leaseagency":
                send_email(email_type="cancel_lease_agency",to_email=lease_order.user.email,context={"lease_order": lease_order})


            return Response({
                "status": 1,
                "message": f"Order cancelled successfully by {cancel_type}.",
                "data": {
                    "order_id": str(lease_order.lease_order_id),
                    "cancelled_by": cancel_type,
                    "cancellation_reason": cancel_reason.reason_name,
                    "updated_at": lease_order.updated_at,
                }
            }, status=200)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong while cancelling lease order.",
                "error": str(e)
            }, status=200)


# class PublicVehicleNameListAPI(APIView):
#     @swagger_auto_schema(
#         operation_summary="Get public vehicle names",
#         operation_description="Returns only vehicle id and (make + model) as name. No authentication required.",
#         responses={
#             1: openapi.Response(description="Vehicle names fetched successfully"),
#             0: openapi.Response(description="No vehicles found")
#         }
#     )
#     def get(self, request):
#         try:
#             auto_update_vehicles_license_expiry()
#             vehicles = Vehicle_Master.objects.filter(active=True)

#             if not vehicles.exists():
#                 return Response({
#                     "status": 0,
#                     "message": "No vehicles found",
#                     "data": []
#                 }, status=200)

#             data = [
#                 {
#                     "id": str(v.id),
#                     "vehicle_make": f"{v.vehicle_make}".strip(),
#                     "vehicle_model": f"{v.vehicle_model}".strip(),
#                     "mfg_year": v.mfg_year
#                 }
#                 for v in vehicles
#             ]

#             return Response({
#                 "status": 1,
#                 "message": "Vehicle names fetched successfully",
#                 "data": data
#             }, status=200)

#         except Exception as e:
#             return Response({
#                 "status": 0,
#                 "message": "Server error",
#                 "error": str(e)
#             }, status=200)

class PublicVehicleNameListAPI(APIView):

    @swagger_auto_schema(
        operation_summary="Get unique unbooked vehicle names",
        operation_description="Returns distinct vehicle make + model only for vehicles that are not booked."
    )
    def get(self, request):
        try:
            auto_update_vehicles_license_expiry()

            BLOCKED_STATUSES = [
                "new_order", "owner_review", "confirmation",
                "invoiced", "invoice_processing", "invoice_paid",
                "scheduled", "active"
            ]

            # Get only active & idle vehicles
            vehicles = (
                Vehicle_Master.objects
                .filter(active=True, vehicle_status__vehicle_status_name__iexact="idle")
                .values("vehicle_make", "vehicle_model", "mfg_year", "id")
            )

            unique_names = {}
            data = []

            for v in vehicles:
                full_name = f"{v['vehicle_make']} {v['vehicle_model']} {v['mfg_year']}".strip()

                # Skip duplicates (unique based on Name)
                if full_name in unique_names:
                    continue

                # Check if booked (skip booked ones)
                is_booked = LeaseOrderMaster.objects.filter(
                    vehicle_id=v["id"],
                    order_status__order_status_name__in=BLOCKED_STATUSES
                ).exists()

                if is_booked:
                    continue

                unique_names[full_name] = True

                data.append({
                    "id": str(v["id"]),
                    "name": full_name,
                    "vehicle_make": v["vehicle_make"],
                    "vehicle_model": v["vehicle_model"],
                    "mfg_year": v["mfg_year"],
                })

            return Response({
                "status": 1,
                "message": "Unique vehicle names fetched successfully",
                "data": data
            }, status=200)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Server error",
                "error": str(e)
            }, status=200)


# class PublicVehicleSearchAPI(APIView):

#     @swagger_auto_schema(
#         operation_summary="Search vehicles by name",
#         operation_description="Search by vehicle make, model, plate number, and filter by date, state, or passenger count.",
#         manual_parameters=[
#             openapi.Parameter('search', openapi.IN_QUERY, description="Search keyword (vehicle make/model/plate)", type=openapi.TYPE_STRING, required=False),
#             openapi.Parameter('start_date', openapi.IN_QUERY, description="Lease start date (YYYY-MM-DD)", type=openapi.TYPE_STRING, required=False),
#             openapi.Parameter('end_date', openapi.IN_QUERY, description="Lease end date (YYYY-MM-DD)", type=openapi.TYPE_STRING, required=False),
#             openapi.Parameter('state', openapi.IN_QUERY, description="Vehicle primary location", type=openapi.TYPE_STRING, required=False),
#             openapi.Parameter('passenger_count', openapi.IN_QUERY, description="Minimum passenger capacity", type=openapi.TYPE_INTEGER, required=False),
#         ],
#         responses={
#             1: openapi.Response(description="Search completed"),
#             0: openapi.Response(description="No vehicles found")
#         }
#     )
#     def get(self, request):
#         try:
#             search = request.query_params.get("search", "").strip()
#             start_date = request.query_params.get("start_date")
#             end_date = request.query_params.get("end_date")
#             state = request.query_params.get("state")
#             passenger_count = request.query_params.get("passenger_count")

#             vehicles = Vehicle_Master.objects.filter(active=True)

#             # 🔍 Keyword search
#             if search:
#                 vehicles = vehicles.filter(
#                     Q(vehicle_make__icontains=search) |
#                     Q(vehicle_model__icontains=search) |
#                     Q(plate_number__icontains=search)
#                 )

#             if state:
#                 vehicles = vehicles.filter(primary_location__icontains=state)

#             if passenger_count:
#                 try:
#                     passenger_count = int(passenger_count)
#                     vehicles = vehicles.filter(passenger_count__gte=passenger_count)
#                 except ValueError:
#                     pass

#             if start_date and end_date:
#                 try:
#                     start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
#                     end_date = datetime.strptime(end_date, "%Y-%m-%d").date()

#                     # Exclude vehicles that have overlapping lease orders
#                     vehicles = vehicles.exclude(
#                         lease_orders__start_date__lte=end_date,
#                         lease_orders__end_date__gte=start_date,
#                         lease_orders__order_status__order_status_name__in=["active", "scheduled"]
#                     )

#                 except ValueError:
#                     pass

#             if not vehicles.exists():
#                 return Response({
#                     "status": 0,
#                     "message": "No matching vehicles found",
#                     "data": []
#                 }, status=200)

#             data = [
#                 {
#                     "id": str(v.id),
#                     "name": f"{v.vehicle_make} {v.vehicle_model}".strip(),
#                     "plate_number": v.plate_number,
#                     "primary_location": v.primary_location,
#                     "passenger_count": v.passenger_count,
#                 }
#                 for v in vehicles
#             ]

#             return Response({
#                 "status": 1,
#                 "message": "Vehicle search results",
#                 "data": data
#             }, status=200)

#         except Exception as e:
#             return Response({
#                 "status": 0,
#                 "message": "Server error",
#                 "error": str(e)
#             }, status=200)

class PublicVehicleSearchAPI(APIView):

    @swagger_auto_schema(
        operation_summary="Public vehicle search",
        operation_description=(
            "Search publicly available vehicles using keyword search and filters. "
            "Supports searching by vehicle make, model, plate number, state, passenger count, "
            "and optional date-range to check booking availability."
        ),
        manual_parameters=[
            openapi.Parameter("vehicle_make", openapi.IN_QUERY, description="Filter by vehicle make", type=openapi.TYPE_STRING),
            openapi.Parameter("vehicle_model", openapi.IN_QUERY, description="Filter by vehicle model", type=openapi.TYPE_STRING),
            openapi.Parameter("mfg_year", openapi.IN_QUERY, description="Filter by manufacturing year", type=openapi.TYPE_INTEGER),
            openapi.Parameter("start_date", openapi.IN_QUERY, description="Desired lease start date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
            openapi.Parameter("end_date", openapi.IN_QUERY, description="Desired lease end date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
            openapi.Parameter("state", openapi.IN_QUERY, description="Vehicle primary location (state/city)", type=openapi.TYPE_STRING),
            openapi.Parameter("passenger_count", openapi.IN_QUERY, description="Minimum seating capacity", type=openapi.TYPE_INTEGER),
            openapi.Parameter("delivery_address", openapi.IN_QUERY, description="Desired delivery address", type=openapi.TYPE_STRING),
        ],
        responses={
            200: openapi.Response(
                description="Vehicle search result",
                examples={
                    "application/json": {
                        "status": 1,
                        "message": "Vehicle search results",
                        "data": [
                            {
                                "id": "uuid",
                                "name": "Toyota Camry",
                                "plate_number": "AB-1234",
                                "primary_location": "Lagos",
                                "passenger_count": 5,
                                "is_booked": False
                            }
                        ]
                    }
                }
            )
        }
    )
    def get(self, request):
        try:
            auto_update_vehicles_license_expiry()
            vehicle_make = request.query_params.get("vehicle_make")
            vehicle_model = request.query_params.get("vehicle_model")
            mfg_year = request.query_params.get("mfg_year")

            start_date = request.query_params.get("start_date")
            end_date = request.query_params.get("end_date")
            state = request.query_params.get("state")
            passenger_count = request.query_params.get("passenger_count")

            delivery_address  = request.query_params.get("delivery_address")

            # Active + Idle vehicles only
            vehicles = Vehicle_Master.objects.filter(
                active=True,
                vehicle_status__vehicle_status_name__iexact="idle"
            )

            # 🔍 Keyword search
            if vehicle_make:
                vehicles = vehicles.filter(vehicle_make__icontains=vehicle_make.strip())

            if vehicle_model:
                vehicles = vehicles.filter(vehicle_model__icontains=vehicle_model.strip())

            if mfg_year:
                try:
                    mfg_year = int(mfg_year)
                    vehicles = vehicles.filter(mfg_year=mfg_year)
                except ValueError:
                    return Response({
                        "status": 0,
                        "message": "Invalid mfg_year value",
                        "data": None
                    }, status=400)

            # if state:
            #     vehicles = vehicles.filter(primary_location__icontains=state)

            if passenger_count:
                try:
                    passenger_count = int(passenger_count)
                    vehicles = vehicles.filter(passenger_count__gte=passenger_count)
                except ValueError:
                    return Response({
                        "status": 0,
                        "message": "Invalid passenger_count value",
                        "data": None
                    }, status=400)

            # --- DATE FILTER (ONLY used for marking booked vehicles) ---
            is_date_filter = False
            if start_date and end_date:
                try:
                    start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
                    end_date = datetime.strptime(end_date, "%Y-%m-%d").date()

                    if start_date > end_date:
                        return Response({
                            "status": 0,
                            "message": "start_date cannot be greater than end_date",
                            "data": None
                        }, status=200)
                    
                    is_date_filter = True
                except:
                    return Response({
                        "status": 0,
                        "message": "Invalid date format. Use YYYY-MM-DD.",
                        "data": None
                    }, status=200)

            if not vehicles.exists():
                return Response({
                    "status": 0,
                    "message": "No matching vehicles found",
                    "data": []
                }, status=200)

            BLOCKED_STATUSES = [
                "new_order", "owner_review", "confirmation",
                "invoiced", "invoice_processing", "invoice_paid",
                "scheduled", "active"
            ]

            data = []

            for v in vehicles:

                is_booked = False

                # Check overlapping booking (only if user selected dates)
                if is_date_filter:
                    overlap = LeaseOrderMaster.objects.filter(
                        vehicle=v,
                        order_status__order_status_name__in=BLOCKED_STATUSES,
                        start_date__lte=end_date,
                        end_date__gte=start_date
                    ).exists()

                    if overlap:
                        is_booked = True
                
                delivery_distance_km = None
                if delivery_address:
                    try:
                        delivery_distance_km = float(
                            get_distance_km(v.primary_location, delivery_address)
                        )
                    except:
                        delivery_distance_km = None

                vehicle_agency = Vehicle_Agency.objects.filter(vehicle_master=v).select_related("lease_agency").first()
                if vehicle_agency:
                    lease_agency = vehicle_agency.lease_agency
                    agency_data = {
                        "id": str(lease_agency.id),
                        "agency_name": lease_agency.business_name,
                        "business_email": lease_agency.business_Email,
                        "business_phone_number": lease_agency.phone_number,
                        "status": vehicle_agency.status,
                        "agency_profile": request.build_absolute_uri(lease_agency.agency_profile.url) if lease_agency.agency_profile else None
                    }
                else:
                    agency_data = {}

                images = Vehicle_Image.objects.filter(vehicle_master=v)
                image_urls = [request.build_absolute_uri(img.image.url) for img in images]

                data.append({
                    "id": str(v.id),
                    "vehicle_make": v.vehicle_make,
                    "vehicle_model": v.vehicle_model,
                    "body_type": v.body_type,
                    "vehicle_status": v.vehicle_status.vehicle_status_name if v.vehicle_status else None,
                    "vehicle_identify_number": v.vehicle_identify_number,
                    "license_renewed_date": v.license_renewed_date,
                    "license_expiry_date": v.license_expiry_date,
                    "insurance_renewed_date":v.insurance_renewed_date,
                    "insurance_expiry_date": v.insurance_expiry_date,
                    "road_worthiness_cert_date": v.road_worthiness_cert_date,
                    "road_worthiness_expiry_date": v.road_worthiness_expiry_date,
                    "engine_spec": v.engine_spec,
                    "other_spec": v.other_spec,
                    "active": v.active,
                    "lease_price_per_day": v.lease_price_per_day,
                    "mfg_year": v.mfg_year,
                    "full_name": f"{v.vehicle_make} {v.vehicle_model}".strip(),
                    "plate_number": v.plate_number,
                    "primary_location": v.primary_location,
                    "delivery_address": delivery_address,
                    "delivery_distance_km": delivery_distance_km,
                    "passenger_count": v.passenger_count,
                    "images": image_urls,
                    "agency": agency_data,
                    "is_booked": is_booked,
                })

            if delivery_address:
                data = sorted(
                    data,
                    key=lambda x: (x["delivery_distance_km"] is None, x["delivery_distance_km"])
                )

            return Response({
                "status": 1,
                "message": "Vehicle search results",
                "data": data
            }, status=200)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Server error",
                "error": str(e)
            }, status=200)


# class PublicVehicleSearchAPI(APIView):

#     @swagger_auto_schema(
#         operation_summary="Search vehicles by name",
#         operation_description="Search by vehicle make or model. Public API.",
#         manual_parameters=[
#             openapi.Parameter(
#                 'search',
#                 openapi.IN_QUERY,
#                 description="Search keyword (vehicle make/model)",
#                 type=openapi.TYPE_STRING,
#                 required=False
#             ),
#         ],
#         responses={
#             1: openapi.Response(description="Search completed"),
#             0: openapi.Response(description="No vehicles found")
#         }
#     )
#     def get(self, request):
#         try:
#             search = request.query_params.get("search", "").strip()

#             vehicles = Vehicle_Master.objects.all()

#             if search:
#                 vehicles = vehicles.filter(
#                     Q(vehicle_make__icontains=search) |
#                     Q(vehicle_model__icontains=search) |
#                     Q(plate_number__icontains=search)
#                 )

#             if not vehicles.exists():
#                 return Response({
#                     "status": 0,
#                     "message": "No matching vehicles found",
#                     "data": []
#                 }, status=200)

#             data = [
#                 {
#                     "id": str(v.id),
#                     "name": f"{v.vehicle_make} {v.vehicle_model}".strip()
#                 }
#                 for v in vehicles
#             ]

#             return Response({
#                 "status": 1,
#                 "message": "Vehicle search results",
#                 "data": data
#             }, status=200)

#         except Exception as e:
#             return Response({
#                 "status": 0,
#                 "message": "Server error",
#                 "error": str(e)
#             }, status=200)

class RiderOrderListAPI(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        security=[{'Bearer': []}],
        operation_summary="Get Rider Orders (Rider Only)",
        operation_description=(
            "Returns all orders assigned to the logged-in **Rider** without using serializers.\n"
            "This API works only if the user_type = Rider."
        )
    )
    def get(self, request):
        try:
            
            auto_cancel_timeout_orders()
            auto_update_scheduled_orders_status()
            # 1. Ensure user is Rider
            
            if request.user.user_type.user_type_name != "Rider":
                return Response({
                    "status": 0,
                    "message": "Only Riders can access this API.",
                    "error": "Permission denied",
                    "data": None
                }, status=200)

            rider = request.user
            # 2. Fetch orders for this rider
            orders = LeaseOrderMaster.objects.filter(user=rider)

            # 3. No orders found
            if not orders.exists():
                return Response({
                    "status": 0,
                    "message": "No orders found for this rider.",
                    "data": []
                }, status=200)

            # 4. Prepare order data manually
            order_list = []
            for order in orders:
                vehicle = order.vehicle
                agency = order.agency

                vehicle_images = []
                if vehicle and hasattr(vehicle, "images"):
                    for img in vehicle.images.all():
                        url = request.build_absolute_uri(img.image.url) if request else img.image.url
                        vehicle_images.append(url)

                order_data = {
                    "lease_order_id": str(order.lease_order_id),
                    "order_number": order.order_number,
                    "start_date": order.start_date,
                    "end_date": order.end_date,
                    "total_amount": order.total_amount,
                    "order_status": order.order_status.order_status_name if order.order_status else None,
                    "total_days":order.total_days,
                    "lease_type":order.lease_type,
                    "leased_for":order.leased_for,
                    "purpose": order.purpose,
                    "client_location":order.client_location,
                    "delivery_address":order.delivery_address,
                    "delivery_distance_km":order.delivery_distance_km,
                    "estimated_delivery_cost":order.estimated_delivery_cost,
                    "driver": {
                        "driver_id": str(order.driver.id),
                        "name": order.driver.name,
                        "email": order.driver.email,
                        "phone_number": order.driver.phone_number
                    } if order.driver else None,
                    "remaining_time": order.remaining_time,
                    "vehicle": {
                        "vehicle_id": str(vehicle.id) if vehicle else None,
                        "plate_number": vehicle.plate_number if vehicle else None,
                        "make": vehicle.vehicle_make if vehicle else None,
                        "model": vehicle.vehicle_model if vehicle else None,
                        "mfg_year": vehicle.mfg_year if vehicle else None,
                        "images": vehicle_images
                    } if vehicle else None,
                    "agency": {
                        "agency_id": str(agency.id) if agency else None,
                        "agency_name": agency.business_name if agency else None,
                        "agency_profile": request.build_absolute_uri(agency.agency_profile.url) if agency and agency.agency_profile else None,
                        "business_email": agency.business_Email,
                        "business_phone_number": agency.phone_number,
                    } if agency else None,
                    "created_at": order.created_at,
                    "updated_at": order.updated_at
                }

                order_list.append(order_data)

            return Response({
                "status": 1,
                "message": "Rider orders fetched successfully.",
                "data": order_list
            }, status=200)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong while fetching rider orders.",
                "error": str(e),
                "data": None
            }, status=200)

class DeactivateVehicleAPI(APIView):
    @swagger_auto_schema(
        operation_description="Deactivate a vehicle by vehicle_id (set active = False)",
        manual_parameters=[
            openapi.Parameter(
                "vehicle_id",
                openapi.IN_QUERY,
                description="UUID of the vehicle to deactivate",
                type=openapi.TYPE_STRING,
                required=True
            )
        ],
        responses={
            1: openapi.Response(description="Vehicle deactivated successfully"),
            0: openapi.Response(description="Vehicle not found"),
        }
    )
    def patch(self, request):
        vehicle_id_input = request.query_params.get("vehicle_id")

        if not vehicle_id_input:
            return Response(
                {"status": 0, "message": "vehicle_id query param is required", "data": None},
                status=200
            )

        try:
            vehicle = Vehicle_Master.objects.get(id=vehicle_id_input)
        except Vehicle_Master.DoesNotExist:
            return Response(
                {"status": 0, "message": "Vehicle not found", "data": None},
                status=200
            )

        vehicle.active = False
        vehicle.save(update_fields=["active"])

        return Response(
            {"status": 1, "message": "Vehicle deactivated successfully", "data": None},
            status=200
        )            

class GetOwnerDriversAPI(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Get Driver List of Vehicle Owner",
        operation_description=(
            "Returns all registered drivers under the authenticated Vehicle Owner account.\n\n"
            "**NOTE:**\n"
            "- This API works only for logged-in Vehicle Owners.\n"
            "- Drivers belonging to other users cannot be accessed even if user_id is passed."
        ),
        responses={
            200: openapi.Response(
                description="Formatted response",
                examples={
                    "application/json": {
                        "success_response": {
                            "status": 1,
                            "message": "Driver list fetched successfully",
                            "data": [
                                {
                                    "id": "e4d8d243-bfd8-4b91-8b56-70e504b1e57a",
                                    "name": "David",
                                    "email": "david@mail.com",
                                    "phone_number": "09022221111"
                                }
                            ]
                        },
                        "error_response": {
                            "status": 0,
                            "message": "Vehicle Owner not found",
                            "data": None
                        }
                    }
                }
            )
        }
    )
    def get(self, request):
        user = request.user

        # validate role
        if user.user_type.user_type_name != "Owner":
            return Response({"status": 0, "message": "Only Vehicle Owners can access drivers", "data": None}, status=200)

        try:
            owner = Vehicle_Owner_Master.objects.get(user_id=user)
        except Vehicle_Owner_Master.DoesNotExist:
            return Response({"status": 0, "message": "Vehicle Owner not found", "data": None}, status=200)

        drivers = Vehicle_Owner_Driver.objects.filter(vehicle_owner=owner)

        if not drivers.exists():
            return Response({"status": 1, "message": "No drivers found", "data": []}, status=200)

        driver_list = [
            {
                "id": str(d.id),
                "name": d.name,
                "email": d.email,
                "phone_number": d.phone_number
            }
            for d in drivers
        ]

        return Response({"status": 1, "message": "Driver list fetched successfully", "data": driver_list}, status=200)

class PolicyDetailAPI(APIView):
    @swagger_auto_schema(
        operation_summary="Get Policy Detail",
        operation_description="Fetch full policy text using policy_name. (E.g., Privacy Policy, Terms and Conditions, Data Protection Policy)",
        manual_parameters=[
            openapi.Parameter(
                name="policy_name",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                required=True,
                description="Name of the policy"
            )
        ],
        responses={
            200: openapi.Response(
                description="Policy detail response",
                examples={
                    "application/json": {
                        "status": 1,
                        "message": "Policy fetched successfully",
                        "data": {
                            "policy_name": "Privacy Policy",
                            "description": "<p>Full HTML policy...</p>"
                        }
                    }
                }
            )
        }
    )
    def get(self, request):
        policy_name = request.query_params.get("policy_name")

        if not policy_name:
            return Response(
                {"status": 0, "message": "policy_name is required", "data": None},
                status=200
            )

        try:
            policy = PolicyMaster.objects.get(policy_name__iexact=policy_name)
            return Response(
                {
                    "status": 1,
                    "message": "Policy fetched successfully",
                    "data": {
                        "policy_name": policy.policy_name,
                        "description": policy.description
                    }
                },
                status=200
            )
        except PolicyMaster.DoesNotExist:
            return Response(
                {"status": 0, "message": "Policy not found", "data": None},
                status=200
            )
        except Exception as e:
            return Response(
                {"status": 0, "message": "Something went wrong", "error": str(e)},
                status=200
            )

from django.shortcuts import get_object_or_404
class DeleteRiderAPI(APIView):
    @swagger_auto_schema(
        operation_summary="Delete Rider",
        operation_description="Delete a rider by user_id (Only Riders can be deleted using this API)",
        manual_parameters=[
            openapi.Parameter(
                name="user_id",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                required=True,
                description="UUID of the Rider (User_Master)"
            )
        ],
    )
    def delete(self, request):
        user_id = request.query_params.get("user_id")

        if not user_id:
            return Response(
                {"status": 0, "message": "user_id is required", "data": None},
                status=200
            )

        try:
            rider = User_Master.objects.get(id=user_id, user_type__user_type_name__iexact="Rider")
            rider.delete()

            return Response(
                {"status": 1, "message": "Rider deleted successfully", "data": None},
                status=200
            )

        except User_Master.DoesNotExist:
            return Response(
                {"status": 0, "message": "Rider not found", "data": None},
                status=200
            )

        except Exception as e:
            return Response(
                {"status": 0, "message": "Something went wrong", "error": str(e)},
                status=200
            )
        
class DeleteLeaseAgencyAPI(APIView):
    @swagger_auto_schema(
        operation_summary="Delete Lease Agency",
        manual_parameters=[
            openapi.Parameter(
                name="user_id",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                required=True,
                description="UUID of User_Master (Lease Agency)"
            )
        ],
    )
    def delete(self, request):
        user_id = request.query_params.get("user_id")

        if not user_id:
            return Response({"status": 0, "message": "user_id is required"}, status=200)

        try:
            user = User_Master.objects.get(id=user_id, user_type__user_type_name="LeaseAgency")

            agency = Lease_Agency_Master.objects.filter(user_id=user).first()
            if agency:
                agency.delete()

            user.delete()

            return Response({"status": 1, "message": "Lease Agency deleted successfully"}, status=200)

        except User_Master.DoesNotExist:
            return Response({"status": 0, "message": "Lease Agency not found"}, status=200)


# class DeleteLeaseAgencyAPI(APIView):
#     @swagger_auto_schema(
#         operation_summary="Delete Lease Agency",
#         operation_description="Delete a lease agency account by agency_id",
#         manual_parameters=[
#             openapi.Parameter(
#                 name="agency_id",
#                 in_=openapi.IN_QUERY,
#                 type=openapi.TYPE_STRING,
#                 required=True,
#                 description="UUID of Lease_Agency_Master"
#             )
#         ],
#     )
#     def delete(self, request):
#         agency_id = request.query_params.get("agency_id")

#         if not agency_id:
#             return Response(
#                 {"status": 0, "message": "agency_id is required", "data": None},
#                 status=200
#             )

#         try:
#             agency = Lease_Agency_Master.objects.get(id=agency_id)
#             user = agency.user_id

#             agency.delete()
#             user.delete()

#             return Response(
#                 {"status": 1, "message": "Lease Agency deleted successfully", "data": None},
#                 status=200
#             )

#         except Lease_Agency_Master.DoesNotExist:
#             return Response(
#                 {"status": 0, "message": "Agency not found", "data": None},
#                 status=200
#             )

#         except Exception as e:
#             return Response(
#                 {"status": 0, "message": "Something went wrong", "error": str(e)},
#                 status=200
#             )

class DeleteVehicleOwnerAPI(APIView):
    @swagger_auto_schema(
        operation_summary="Delete Vehicle Owner",
        manual_parameters=[
            openapi.Parameter(
                name="user_id",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                required=True,
                description="UUID of User_Master (Vehicle Owner)"
            )
        ],
    )
    def delete(self, request):
        user_id = request.query_params.get("user_id")

        if not user_id:
            return Response({"status": 0, "message": "user_id is required"}, status=200)

        try:
            user = User_Master.objects.get(id=user_id, user_type__user_type_name="Owner")

            owner = Vehicle_Owner_Master.objects.filter(user_id=user).first()
            if owner:
                owner.delete()   # cascades drivers, vehicles, images

            user.delete()

            return Response({"status": 1, "message": "Vehicle Owner deleted successfully"}, status=200)

        except User_Master.DoesNotExist:
            return Response({"status": 0, "message": "Vehicle Owner not found"}, status=200)

# class DeleteVehicleOwnerAPI(APIView):
#     @swagger_auto_schema(
#         operation_summary="Delete Vehicle Owner",
#         operation_description="Delete a vehicle owner and all related data (drivers, vehicles, images, orders)",
#         manual_parameters=[
#             openapi.Parameter(
#                 name="owner_id",
#                 in_=openapi.IN_QUERY,
#                 type=openapi.TYPE_STRING,
#                 required=True,
#                 description="UUID of Vehicle_Owner_Master"
#             )
#         ],
#     )
#     def delete(self, request):
#         owner_id = request.query_params.get("owner_id")

#         if not owner_id:
#             return Response(
#                 {"status": 0, "message": "owner_id is required", "data": None},
#                 status=200
#             )

#         try:
#             owner = Vehicle_Owner_Master.objects.get(id=owner_id)
#             user = owner.user_id

#             owner.delete()
#             user.delete()

#             return Response(
#                 {"status": 1, "message": "Vehicle Owner deleted successfully", "data": None},
#                 status=200
#             )

#         except Vehicle_Owner_Master.DoesNotExist:
#             return Response(
#                 {"status": 0, "message": "Vehicle Owner not found", "data": None},
#                 status=200
#             )

#         except Exception as e:
#             return Response(
#                 {"status": 0, "message": "Something went wrong", "error": str(e)},
#                 status=200
            # )

from django.core.files.storage import default_storage
class ImageConverter(APIView):
    def post(self, request):
        try:
            image_file = request.FILES.get('image')

            if not image_file:
                return Response({
                    'status': 0,
                    'message': 'No image provided'
                }, status=200)

            # Generate unique file name
            ext = image_file.name.split('.')[-1]
            file_name = f"{uuid.uuid4()}.{ext}"

            # Save file to MEDIA folder
            file_path = os.path.join('uploads/', file_name)
            saved_path = default_storage.save(file_path, image_file)

            # Build full image URL
            image_url = request.build_absolute_uri(settings.MEDIA_URL + saved_path)

            return Response({
                'status': 1,
                'image_url': image_url
            }, status=200)

        except Exception as e:
            return Response({
                'status': 0,
                'message': str(e)
            }, status=200)


