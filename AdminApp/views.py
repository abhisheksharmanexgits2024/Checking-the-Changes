import random
from django.shortcuts import render
from requests import Session
from .models import *
from .serializers import *
from django.conf import settings
from django.utils import timezone
from rest_framework import status
from django.contrib.auth import logout
from rest_framework.views import APIView
from datetime import timedelta
from rest_framework.response import Response
from django.contrib.auth.hashers import make_password
from django.contrib.auth.hashers import check_password
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.contrib.auth import logout
from django.contrib.sessions.backends.db import SessionStore 
from rest_framework_simplejwt.tokens import RefreshToken # type: ignore
from rest_framework.permissions import IsAuthenticated
#from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import MultiPartParser, FormParser
from AdminApp.pagination import CustomPagination
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from User.models import *


################# register process #################
import logging
logger = logging.getLogger(__name__)

ORDER_TIMEOUT_STATUSES = [
    "new_order", "owner_review", "confirmation", "owner_declined", 
    "invoiced","invoice_processing","rider_declined"
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

class UserRegistrationAPI(APIView):
    def post(self, request):
        data = request.data
        print("data:---",data)
        email = data.get('email')
        phone_number =  data.get('phone_number')

        value = str(phone_number)
        if not value.startswith("+") or not value[1:].isdigit(): # number muset start with + or only digits are allowed
            return Response({'status': 0, "message": " Mobile number must start with + and contain only digits. ", 'data': None}, status=status.HTTP_200_OK)
        if not re.match(r'^\+?[1-9]\d{9,14}$', value):  # Regex for valid mobile numbers
            return Response({'status': 0, "message": "Invalid mobile number format. Use digits only (e.g., +14155552671 or 919876543210).", 'data': None}, status=status.HTTP_200_OK)

        if User_Master.objects.filter(email = email).exists():
            return Response({"status":0 ,"message":"this email is already registered.","data":None},status=status.HTTP_200_OK)
        
        if User_Master.objects.filter(phone_number = phone_number).exists():
            return Response({"status":0, "messgae":"this phone number is already registered. ","data":None},status=status.HTTP_200_OK)


        serializer = TempUserSerializer(data=data)
        print("serializer:--",serializer)
        if serializer.is_valid():
            otp = 1111
            print("otp:--",otp)
            user = serializer.save(otp=otp, created_at=timezone.now(), otp_time_limit = timezone.now() + timedelta(days=1))
            print("user:---",user)

            #send otp on email
            subject = 'Account Verification OTP'
            html_message = render_to_string('myadmin/email_otp.html',{'user':user,'otp':otp})

            email_message = EmailMultiAlternatives(
                subject,
                f"Your Registration OTP is {otp}. It is valid for 3 minutes.",
                settings.EMAIL_HOST_USER,
                [user.email]
            )

            email_message.attach_alternative(html_message, "text/html")
            email_message.send(fail_silently=False)

            return Response({'status': 1, "message": "OTP sent to your email. Please verify your account.", 'data': {"details": serializer.data}}, status=status.HTTP_201_CREATED)
        print("error:--",serializer.errors)
        return Response({'status': 0, 'message':'Invalid data','data':serializer.errors},status=status.HTTP_200_OK)
    
class OtpVerificationAPI(APIView):
    def post(self, request):
        data = request.data
        serializer = OTPVerificationSerializer(data=data)

        if serializer.is_valid():
            email = serializer.validated_data['email']
            otp = serializer.validated_data['otp']

            user = AdminTempUser.objects.filter(email=email).order_by('-created_at').first()

            if not user:
                return Response({"status":0, "message":"User Not Found, Please Register First.","data":None},status=status.HTTP_200_OK)
            
            if user.otp_time_limit and user.otp_time_limit < timezone.now():
                return Response({'status': 0, 'message': 'OTP expired. Please request a new one.', 'data': None},status=status.HTTP_200_OK)

            if str(user.otp) != str(otp):
                return Response({'status': 0, 'message': 'Invalid OTP. Please try again.', 'data': None}, status=status.HTTP_200_OK)

            #  Assign default user type
            
            default_type, _ = User_Type.objects.get_or_create(user_type_name="Admin")

            registered_user = User_Master.objects.create(
                first_name=user.first_name,
                phone_number=user.phone_number,
                email=user.email,
                password = make_password(user.password),
                user_type=default_type  #  Required field
            )

            session = SessionStore()
            session["user_id"]=str(registered_user.id)
            session.create()

            session = CustomSession.objects.create(
                user=registered_user,
                session_key=session.session_key,
                ip_address=request.META.get('REMOTE_ADDR'),
                expire_date=timezone.now() + timedelta(days=1)
            )

            user.delete()

            return Response({
                "status": 1,
                "message": "Account successfully verified",
                "data": [{'session-key': str(session.session_key)}]
            }, status=status.HTTP_201_CREATED)
            
        return Response({"status":0,"message":"Invalid data","data":serializer.errors},status=status.HTTP_200_OK)


class OtpResendAPI(APIView):
    def post(self, request):
        email = request.data.get('email')

        if not email:
            return Response({'status':0, 'message':'Email is required.','data':None},status=status.HTTP_200_OK)
        
        try:
            
            temp_user = AdminTempUser.objects.filter(email=email).order_by('-created_at').first()
            print(temp_user)

            if not temp_user:
                return Response({'status': 0,'message':'Email Not Found!','data':None},status=status.HTTP_200_OK)
            
            current_time = timezone.now()
            otp_validity_period = timedelta(minutes=2)

            if not temp_user.otp_time_limit:
                temp_user.otp_time_limit = current_time + otp_validity_period
                temp_user.save()

            # check otp is still valid 
            if current_time <= temp_user.otp_time_limit:
                otp = temp_user.otp #resend otp 
            
            else:
                # genrate new otp 
                otp = 1111
                print(otp)
                temp_user.otp = otp
                temp_user.otp_time_limit = current_time + otp_validity_period
                temp_user.save()

            # send otp via email
            subject = "Your Registration Resend Verification OTP"
            html_message = render_to_string('myadmin/email_otp.html', {'user': temp_user, 'otp': otp})
            recipient_email = temp_user.email  

            email_message = EmailMultiAlternatives(
                subject,
                f"Your OTP is {otp}. It is valid for 3 minutes.",
                settings.EMAIL_HOST_USER,
                [recipient_email] 
            )
            email_message.attach_alternative(html_message, "text/html")
            email_message.send(fail_silently=False)

            return Response({
                "status": 1,
                "message": f"OTP sent to your email. It is valid for 3 minutes.",
                "data": {
                    "email": temp_user.email
                }}, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({'status':0 ,'messgae': str(e), 'data':None},status=status.HTTP_200_OK)
        
class UserProfile_DataAPI(APIView):
    permission_classes =[IsAuthenticated]
    def get(self, request, id=None):
        session_key = request.headers.get('session-key')

        if not session_key:
            return Response({"status": 0, "message": "Session is not found!", "data": None}, status=status.HTTP_200_OK)

        try:
            session = CustomSession.objects.get(session_key=session_key)
            if session.expire_date < timezone.now():
                return Response({"status": 0, "message": "Session expired! Login again.", "data": None}, status=status.HTTP_200_OK)
            current_user = session.user
        except CustomSession.DoesNotExist:
            return Response({"status": 0, "message": "Invalid session!", "data": None}, status=status.HTTP_200_OK)

        if current_user.account_status != 'Active':
            return Response({'status': 0, 'message': 'Account is deactivated.', 'data': None}, status=status.HTTP_200_OK)

        #  Accessing a specific user's profile
        if id:
            try:
                profile_data = User_Type.objects.get(id=id)

                # Restriction logic
                if current_user.role == 'User' and str(current_user.id) != str(id):
                    return Response({'status': 0, 'message': 'Users can only access their own profile.', 'data': None}, status=status.HTTP_200_OK)

                if current_user.role == 'Moderator':
                    # Can access own profile or any User
                    if profile_data.role not in ['User'] and str(profile_data.id) != str(current_user.id):
                        return Response({"status": 0, 'message': 'Moderators can only view Users or their own profile.'}, status=status.HTTP_200_OK)

                if current_user.role == 'Admin' and profile_data.role == 'Super_Admin':
                    return Response({"status": 0, 'message': 'Admins cannot view Super_Admin profiles.'}, status=status.HTTP_200_OK)

                serializer = GetRegistrationSerializer(profile_data)
                return Response({'status': 1, 'message': 'User data fetched successfully', 'data': serializer.data}, status=status.HTTP_201_CREATED)

            except User_Master.DoesNotExist:
                return Response({'status': 0, 'message': 'User not found', 'data': None}, status=status.HTTP_200_OK)

        if current_user.role == 'Super_Admin':
                profile_data = User_Master.objects.all()

        elif current_user.role == 'Admin':
            profile_data = User_Master.objects.exclude(role__iexact='Super_Admin')  

        elif current_user.role == 'Moderator':  
            profile_data = User_Master.objects.filter(
                models.Q(role__iexact='User') | models.Q(id=current_user.id)
            )
            
        elif current_user.role == 'User':
            profile_data = User_Master.objects.filter(models.Q(id=current_user.id))

        else:
             return Response({"status": 0, 'message': 'Access denied.', 'data': None}, status=status.HTTP_200_OK)


        serializer = GetRegistrationSerializer(profile_data, many=True)
        return Response({"status": 1, "message": "Users retrieved successfully.", "data": serializer.data}, status=status.HTTP_201_CREATED)


################# LOGIN WITH OTP #################


class LoginAPI(APIView):
    @swagger_auto_schema(
        operation_summary="Admin Login",
        operation_description="Admin login using email and password. Sends OTP to registered email.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['email', 'password'],
            properties={
                'email': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    format=openapi.FORMAT_EMAIL,
                    description="Admin email address"
                ),
                'password': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    format=openapi.FORMAT_PASSWORD,
                    description="Admin account password"
                ),
            },
        ),
        responses={
            200: openapi.Response(
                description="OTP sent or validation error",
                examples={
                    "application/json": {
                        "status": 1,
                        "message": "OTP sent to your email. Please verify."
                    }
                }
            )
        }
    )
    def post(self, request):
        try:
            email = request.data.get('email')
            password = request.data.get('password')

            if not email:
                return Response({'status': 0, 'message': 'Email are required', 'data': None}, status=status.HTTP_200_OK)
                
            if not password:
                return Response({'status': 0, 'message':'password are required','data':None}, status=status.HTTP_200_OK)

            # Fetch user
            try:
                user = User_Master.objects.get(email=email)
            except User_Master.DoesNotExist:
                return Response({'status': 0, 'message': 'Email not registered. Please sign up.', 'data': None},status=status.HTTP_200_OK)

            if user.account_status != 'Active':
                return Response({'status': 0, 'message': 'Your account is deactivated. Please contact support.', 'data': None},status=status.HTTP_200_OK)

            if user.user_type.user_type_name != "Admin":
                return Response({'status': 0, 'message': 'Only administrators are authorized to access this resource.', 'data': None},status=status.HTTP_200_OK)
            # Validate password
            if not check_password(password, user.password):
                return Response({'status': 0, 'message': 'Invalid Credentials', 'data': None},
                                status=status.HTTP_200_OK)

            # Delete old OTPs
            AdminLoginOTP.objects.filter(user=user).delete()

            # Generate new OTP (3 minutes expiry)
            otp = 1111
            print(otp)
            login_otp = AdminLoginOTP.objects.create(
                user=user,
                otp=otp,
                otp_time_limit=timezone.now() + timedelta(minutes=2)
            )

            # Send OTP via email
            subject = "Your login Verification OTP"
            html_message = render_to_string('myadmin/email_otp.html', {'user': user, 'otp': otp})
            email_message = EmailMultiAlternatives(
                subject,
                f"Your OTP is {otp}. It is valid for 3 minutes.",
                settings.EMAIL_HOST_USER,
                [user.email]
            )
            email_message.attach_alternative(html_message, "text/html")
            email_message.send(fail_silently=False)

            return Response({'status': 1, 'message': 'OTP sent to your email. Please verify.'},status=status.HTTP_200_OK)

        except Exception as e:
            return Response({'status': 0, 'message': 'Internal Server Error', 'data': str(e)},status=status.HTTP_200_OK)


################# VERIFY LOGIN OTP #################

class VerifyLoginAPI(APIView):
    def post(self, request):
        try:
            email = request.data.get('email')
            otp = request.data.get('otp')

            if not email or not otp:
                return Response({'status': 0, 'message': 'Email and OTP required'}, status=status.HTTP_200_OK)

            # Fetch user and latest OTP
            try:
                user = User_Master.objects.get(email=email)
                login_otp = AdminLoginOTP.objects.filter(user=user).order_by('-created_at_otp').first()
            except User_Master.DoesNotExist:
                return Response({'status': 0, 'message': 'Email not registered', 'data': None}, status=status.HTTP_200_OK)

            if not login_otp:
                return Response({'status': 0, 'message': 'OTP not found for this user.'}, status=status.HTTP_200_OK)

            # Check OTP expiry
            if login_otp.otp_time_limit and login_otp.otp_time_limit < timezone.now():
                return Response({'status': 0, 'message': 'OTP expired. Please request a new one.'}, status=status.HTTP_200_OK)

            # Check OTP match
            if str(login_otp.otp) != str(otp):
                return Response({'status': 0, 'message': 'Invalid OTP. Please try again.'}, status=status.HTTP_200_OK)

            # Log activity
            AdminActivityLog.objects.create(user=user, action='Login')

            # Generate JWT tokens
            refresh = RefreshToken.for_user(user)
            access_token = str(refresh.access_token)
            refresh_token = str(refresh)

            # Create Django session
            django_session = SessionStore()
            django_session["user_id"] = str(user.id)
            django_session.create()

            # Create custom session
            custome_session = CustomSession.objects.create(
                session_key=django_session.session_key,
                user=user,
                ip_address=request.META.get('REMOTE_ADDR'),
                expire_date=timezone.now() + timedelta(days=1)
            )

            # Clear OTP
            login_otp.delete()

            return Response({
                'status': 1,
                'message': 'Login successful',
                'data': {
                    'session-key': custome_session.session_key,
                    'access_token': access_token,
                    'refresh_token': refresh_token,
                    'user': {
                        'id': str(user.id),
                        'first_name': user.first_name,
                        'email':user.email,
                        'user_type': user.user_type.user_type_name if user.user_type else None,
                        'account_status': user.account_status,
                    }
                }
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({'status': 0, 'message': str(e)}, status=status.HTTP_200_OK)



class LoginOtpResendAPI(APIView):
    def post(self, request):
        email = request.data.get('email')

        if not email:
            return Response({'status': 0, 'message': 'Email is required.', 'data': None},status=status.HTTP_200_OK)

        try:
         
            try:
                user = User_Master.objects.get(email=email)
            except User_Master.DoesNotExist:
                return Response({'status': 0, 'message': 'User with this email does not exist.', 'data': None},status=status.HTTP_200_OK)

            temp_user = AdminLoginOTP.objects.filter(user=user).order_by('-created_at_otp').first()

            if not temp_user:
                return Response({'status': 0, 'message': 'OTP record not found for this user.', 'data': None}, status=status.HTTP_200_OK)

            # OTP 
            current_time = timezone.now()
            otp_validity_period = timedelta(minutes=2)

            if not temp_user.otp_time_limit:
                temp_user.otp_time_limit = current_time + otp_validity_period
                temp_user.save()

            if current_time <= temp_user.otp_time_limit:
                otp = temp_user.otp  # resend otp
            else:
                otp = 1111
                print("resend otp:- ",otp)
                temp_user.otp = otp
                temp_user.otp_time_limit = current_time + otp_validity_period
                temp_user.save()

            # send otp via email
            subject = "Your Login Resend Verification OTP"
            html_message = render_to_string('myadmin/email_otp.html', {'user': user, 'otp': otp})

            email_message = EmailMultiAlternatives(
                subject,
                f"Your OTP is {otp}. It is valid for 3 minutes.",
                settings.EMAIL_HOST_USER,
                [user.email]
            )
            email_message.attach_alternative(html_message, "text/html")
            email_message.send(fail_silently=False)

            return Response({
                "status": 1,
                "message": "OTP sent to your email. It is valid for 3 minutes.",
                "data": {
                    "email": user.email
                }}, status=201)

        except Exception as e:
            return Response({'status': 0, 'message': str(e), 'data': None},status=status.HTTP_200_OK)



class ForgotPasswordAPI(APIView):
    def post(self, request):
        email = request.data.get("email")  

        if not email:
            return Response({'status':0 ,'message':'Email are required','data':None},status=status.HTTP_200_OK)
        
        try:
            user = User_Master.objects.get(email = email)

            otp = 1111
            created = User_OTP_Master.objects.update_or_create(
                user=user,
                defaults = {'otp': otp, 'created_at': timezone.now()} 
            )

            subject = "Your Forgot Password Verification OTP"
            html_message = render_to_string('myadmin/email_otp.html', {'user': user, 'otp': otp})
            recipient_email = user.email 

            print(f"Sending OTP email to: {recipient_email}")

            email_message = EmailMultiAlternatives(
                subject,
                f"Your Forgot Password OTP is {otp}. It is valid for 3 minutes.",
                settings.EMAIL_HOST_USER,
                [recipient_email] 
            )
            email_message.attach_alternative(html_message, "text/html")
            email_message.send(fail_silently=False)

            return Response({
                'status': 1,
                'message': 'OTP sent to your email',
                'data':[{'otp expired in': '3 min'}]
            }, status=201)
        
        except User_Master.DoesNotExist:
            return Response({'status': 0, 'message': 'Email not registered','data':None}, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({'status': 0, 'message': 'Internal Server Error', 'data': str(e)},status=status.HTTP_200_OK)            


### Forgot Verification Process ###
class Forgot_Otp_API(APIView):
    def post(self, request):
        email = request.data.get('email')
        otp = request.data.get('otp')

        if not email or not otp:
            return Response({'status': 0, 'message': 'Email and OTP are required','data':None}, status=status.HTTP_200_OK)

        forgot_entry = User_OTP_Master.objects.filter(user__email=email, otp=otp).first()
        if not forgot_entry:
            return Response({'status': 0, 'message': 'Invalid OTP','data':None}, status=status.HTTP_200_OK)

        # Check if OTP is expired (3 minutes)
        otp_expiry_time = forgot_entry.created_at + timedelta(minutes=2)
        if timezone.now() > otp_expiry_time:
            return Response({'status': 0, 'message': 'OTP expired. Please request a new one.','data':None}, status=status.HTTP_200_OK)

        return Response({'status': 1, 'message': 'OTP verified. Proceed to reset password.','data':None}, status=status.HTTP_200_OK)


### Resend Process ###

class Resend_Forgot_Otp_API(APIView):
    def post(self, request):
        email = request.data.get("email")
        print("resend_email:--",email)

        if not email:
            return Response({'status': 0, 'message': 'Email is required','data':None}, status=status.HTTP_200_OK)

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
                otp = otp_entry.otp  # Resend existing OTP
            else:
                # Generate a new OTP and update the time limit
                otp = 1111
                otp_entry.otp = otp
                otp_entry.created_at = current_time  # Update timestamp
                otp_entry.save()

            # Send OTP via email
            subject = "Your Resend OTP for forgot password Verification"
            html_message = render_to_string('myadmin/email_otp.html', {'user': user, 'otp': otp})
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
                'message': f"OTP sent to your email. It is valid for 3 minutes.",
                'data': {
                    'email': user.email,
                    'otp_valid_till': otp_expiry_time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            }, status=status.HTTP_201_CREATED)

        except (User_Master.DoesNotExist):
            return Response({"status": 0, "message": "Email not found. Please register first.",'data':None}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({'status': 0, 'message': 'Internal Server Error', 'error': str(e)}, status=status.HTTP_200_OK)


class Reset_Password_API(APIView):
    def validate_password(self,value):
        if len(value) < 8:
            raise serializers.ValidationError({'status':0, "error": "InvalidPasswordFormat",
                                                "message": "Password must be at least 8 characters long.","data":(None)},status=status.HTTP_200_OK)
        if not re.search(r'[A-Z]',value):
            raise serializers.ValidationError({'status':0,"error": "InvalidPasswordFormat", 
                                               "message": "Password must include at least one uppercase letter.","data":(None)},status=status.HTTP_200_OK)
        if not re.search(r'\d',value):
            raise serializers.ValidationError({'status':0,"error": "InvalidPasswordFormat", 
                                              "message":"password must contain at least one numeric charecter","data":(None)},status=status.HTTP_200_OK)
        if not re.search(r'[!@#$_%^&*(),.?":{}|<>]', value):  
            raise serializers.ValidationError({
            'status':0,
            "error": "InvalidPasswordFormat",
            "message": "Password must contain at least one special character.",
            "data":(None)
        },status=status.HTTP_200_OK)
            
        return value
     
    def post(self, request):
        new_password = request.data.get('new_password')
        confirm_password = request.data.get('confirm_password')

        if not all([new_password, confirm_password]):
            return   Response({'status': 0, 'message': 'Both new_password and confirm_password are required','data':None}, status=status.HTTP_200_OK)

        email = request.data.get('email')  # Fetch stored email
        if not email:
            return Response({'status': 0, 'message': 'Email not found. Try again.','data':None}, status=status.HTTP_200_OK)

        if new_password != confirm_password:
            return Response({'status': 0, 'message': 'Passwords do not match','data':None}, status=status.HTTP_200_OK)

        try:
            self.validate_password(new_password)
        
            user = User_Master.objects.get(email=email)

            # Check if new password matches old password
            if check_password(new_password, user.password):
                return Response({'status': 0, 'message': 'You cannot use your previous password. Try another password.','data':None}, status=status.HTTP_200_OK)

            # Hash and update the new password
            user.password = make_password(new_password)
            user.save()

            # Delete OTP 
            User_OTP_Master.objects.filter(user=user).delete()

            return Response({'status': 1, 'message': 'Password reset successfully. You can now log in.','data':None}, status=status.HTTP_201_CREATED)

        except User_Master.DoesNotExist:
            return Response({'status': 0, 'message': 'Email not registered', 'data': None}, status=status.HTTP_200_OK)

        except serializers.ValidationError as ve:
            # Handles any password validation error
            return Response(ve.detail, status=200)
           
        except Exception as e:
            return Response({'status': 0, 'message': 'Internal Server Error', 'data': str(e)},status=200)
        
class LeaseOrderAPI(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request, order_number=None):
        try:
            user = request.user
            auto_cancel_timeout_orders()
            auto_update_scheduled_orders_status()
            if user.user_type.user_type_name != "Admin":
                return Response({'status': 0, 'message': 'Only administrators are authorized to access this resource.', 'data': None},status=status.HTTP_200_OK)        
            if order_number:
                order = (
                    LeaseOrderMaster.objects
                    .select_related("vehicle", "agency", "order_status", "user")
                    .filter(order_number=order_number)
                    .first()
                )

                if not order:
                    return Response({
                        "status": 0,
                        "message": "Order not found.",
                        "data": None
                    }, status=200)

                serializer = LeaseOrderDetailSerializer(order, context={"request": request})
                return Response({
                    "status": 1,
                    "message": "Order detail fetched successfully.",
                    "data": serializer.data
                }, status=200)


            logs = (
                LeaseOrderMaster.objects
                .select_related("vehicle", "agency", "order_status", "user")
                .all()
                .order_by("-updated_at")
            )

            paginator = CustomPagination()
            result_page = paginator.paginate_queryset(logs, request)

            serializer = LeaseOrderLogSerializer(result_page, many=True, context={"request": request})
            total_pages = paginator.page.paginator.num_pages
            
            return paginator.get_paginated_response({
                "status": 1,
                "message": "Lease order logs fetched successfully.",
                "total_pages": total_pages,
                "data": serializer.data
            })

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong while fetching lease orders.",
                "error": str(e),
                "data": None
            }, status=200)

class ScheduleLeaseOrderAPI(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request, order_number=None):
        try:
            user = request.user
            auto_cancel_timeout_orders()
            auto_update_scheduled_orders_status()
            if user.user_type.user_type_name != "Admin":
                return Response({'status': 0, 'message': 'Only administrators are authorized to access this resource.', 'data': None},status=status.HTTP_200_OK)
            if order_number:
                order = (
                    LeaseOrderMaster.objects
                    .select_related("vehicle", "agency", "order_status", "user")
                    .filter(order_number=order_number)
                    .first()
                )

                if not order:
                    return Response({
                        "status": 0,
                        "message": "Order not found.",
                        "data": None
                    }, status=200)

                serializer = LeaseOrderDetailSerializer(order, context={"request": request})
                return Response({
                    "status": 1,
                    "message": "Order detail fetched successfully.",
                    "data": serializer.data
                }, status=200)

            logs = (
                LeaseOrderMaster.objects
                .select_related("vehicle", "agency", "order_status", "user")
                .filter(order_status__order_status_name__in=["scheduled", "active", "completed"])
                .order_by("-updated_at")
            )

            paginator = CustomPagination()
            result_page = paginator.paginate_queryset(logs, request)

            serializer = LeaseOrderLogSerializer(
                result_page,
                many=True,
                context={"request": request}
            )
            total_pages = paginator.page.paginator.num_pages
            return paginator.get_paginated_response({
                "status": 1,
                "message": "Lease order logs fetched successfully.",
                "total_pages": total_pages,
                "data": serializer.data
            })

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong while fetching schedule lease orders.",
                "error": str(e),
                "data": None
            }, status=200)    

class VehicleInventoryAPI(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request,vehicle_id=None):
        try:
            user = request.user
            if user.user_type.user_type_name != "Admin":
                return Response({'status': 0, 'message': 'Only administrators are authorized to access this resource.', 'data': None},status=status.HTTP_200_OK)
            if vehicle_id:
                vehicle = (
                    Vehicle_Master.objects
                    .select_related("vehicle_owner", "vehicle_status")
                    .filter(id=vehicle_id)
                    .first()
                )

                if not vehicle:
                    return Response({
                        "status": 0,
                        "message": "Vehicle not found.",
                        "data": None
                    }, status=200)

                serializer = VehicleDetailSerializer(
                    vehicle,
                    context={"request": request}
                )

                return Response({
                    "status": 1,
                    "message": "Vehicle details fetched successfully.",
                    "data": serializer.data
                }, status=200)

            vehicles = (
                Vehicle_Master.objects
                .select_related("vehicle_owner", "vehicle_status")
                .all()
            )

            paginator = CustomPagination()
            result_page = paginator.paginate_queryset(vehicles, request)

            serializer = GlobalVehicleInventorySerializer(
                result_page,
                many=True,
                context={"request": request}
            )
            total_pages = paginator.page.paginator.num_pages
            return paginator.get_paginated_response({
                "status": 1,
                "message": "Vehicles fetched successfully.",
                "total_pages": total_pages,
                "data": serializer.data
            })

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong while fetching vehicle inventory.",
                "error": str(e),
                "data": None
            }, status=200)    

class CarOwnersAPI(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request,owner_id=None):
        try:
            user = request.user
            if user.user_type.user_type_name != "Admin":
                return Response({'status': 0, 'message': 'Only administrators are authorized to access this resource.', 'data': None},status=status.HTTP_200_OK)            
            if owner_id:
                owner = (
                    Vehicle_Owner_Master.objects
                    .select_related("user_id", "agency")
                    .filter(id=owner_id)
                    .first()
                )

                if not owner:
                    return Response({
                        "status": 0,
                        "message": "Owner not found.",
                        "data": None
                    }, status=200)

                serializer = CarOwnerDetailSerializer(
                    owner,
                    context={"request": request}
                )

                return Response({
                    "status": 1,
                    "message": "Owner fetched successfully.",
                    "data": serializer.data
                }, status=200)

            owners = (
                Vehicle_Owner_Master.objects
                .select_related("user_id", "agency")
                .order_by("-created_at")
            )

            paginator = CustomPagination()
            result_page = paginator.paginate_queryset(owners, request)

            serializer = CarOwnersLogSerializer(
                result_page,
                many=True,
                context={"request": request}
            )
            total_pages = paginator.page.paginator.num_pages
            return paginator.get_paginated_response({
                "status": 1,
                "message": "Owners fetched successfully.",
                "total_pages": total_pages,
                "data": serializer.data
            })

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong while fetching owners.",
                "error": str(e),
                "data": None
            }, status=200)

# Vehicles Log API

class VehicleLogAPI(APIView):
    permission_classes = [IsAuthenticated]
    @swagger_auto_schema(
        operation_summary="Vehicle Log API - Vehicle Detail / Owner Vehicles / Vehicle Owners",
        operation_description=(
            "This endpoint has three behaviors based on query parameters:\n\n"
            "1️⃣ **If `vehicle_id` is provided** → returns single vehicle details.\n"
            "2️⃣ **If `owner_id` is provided** → returns all vehicles under that owner (paginated).\n"
            "3️⃣ **If no parameters provided** → returns vehicle owners list with statistics (paginated)."
        ),

        manual_parameters=[
            openapi.Parameter(
                name="vehicle_id",
                in_=openapi.IN_QUERY,
                description="Fetch specific vehicle details",
                type=openapi.TYPE_STRING,
                required=False
            ),
            openapi.Parameter(
                name="owner_id",
                in_=openapi.IN_QUERY,
                description="Fetch all vehicles under a specific owner",
                type=openapi.TYPE_STRING,
                required=False
            ),
            openapi.Parameter(
                name="page",
                in_=openapi.IN_QUERY,
                description="Page number for pagination",
                type=openapi.TYPE_INTEGER,
                required=False
            ),
        ],

        responses={
            200: openapi.Response(
                description="Vehicle log response",
                examples={
                    "application/json": {
                        "status": 1,
                        "message": "Vehicle details fetched successfully.",
                        "data": {
                            "id": 12,
                            "plate_number": "ABC123",
                            "vehicle_make": "Toyota",
                            "vehicle_model": "Corolla",
                            "status": "Active",
                            "owner": {
                                "name": "John Doe",
                                "phone": "9999999999"
                            }
                        }
                    }
                }
            )
        }
    )
    def get(self, request):
        try:
            user = request.user
            if user.user_type.user_type_name != "Admin":
                return Response({'status': 0, 'message': 'Only administrators are authorized to access this resource.', 'data': None},status=status.HTTP_200_OK)            
            owner_id = request.GET.get("owner_id")
            vehicle_id = request.GET.get("vehicle_id")

            if vehicle_id:
                vehicle = (
                    Vehicle_Master.objects
                    .filter(id=vehicle_id)
                    .select_related("vehicle_owner", "vehicle_status")
                    .first()
                )

                if not vehicle:
                    return Response({
                        "status": 0,
                        "message": "Vehicle not found.",
                        "data": None
                    }, status=200)

                serializer = OwnerVehicleDetailSerializer(
                    vehicle,
                    context={"request": request}
                )

                return Response({
                    "status": 1,
                    "message": "Vehicle details fetched successfully.",
                    "data": serializer.data
                }, status=200)

            if owner_id:
                owner = Vehicle_Owner_Master.objects.filter(id=owner_id).first()

                if not owner:
                    return Response({
                        "status": 0,
                        "message": "Owner not found.",
                        "data": None
                    }, status=200)

                # Get all vehicles owned by this owner
                vehicles = (
                    Vehicle_Master.objects
                    .filter(vehicle_owner=owner)
                    .select_related("vehicle_owner", "vehicle_status")
                    .order_by("-created_at")
                )

                paginator = CustomPagination()
                paginated = paginator.paginate_queryset(vehicles, request)

                serializer = AgencyVehiclesSerializer(
                    paginated,
                    many=True,
                    context={"request": request}
                )
                total_pages = paginator.page.paginator.num_pages
                return paginator.get_paginated_response({
                    "status": 1,
                    "message": "Vehicles under this owner fetched successfully.",
                    "total_pages": total_pages,
                    "data": serializer.data
                })

            owners = Vehicle_Owner_Master.objects.select_related("user_id").order_by("-created_at")

            paginator = CustomPagination()
            paginated_owners = paginator.paginate_queryset(owners, request)

            result = []

            for owner in paginated_owners:

                # 1️⃣ Count vehicles owned by this owner
                vehicles_owned = Vehicle_Master.objects.filter(vehicle_owner=owner)
                nos_of_vehicles = vehicles_owned.count()

                # 2️⃣ Agencies mapped via Vehicle_Agency (DISTINCT)
                agencies_connected = (
                    Vehicle_Agency.objects
                    .filter(vehicle_master__vehicle_owner=owner, status="Active")
                    .values_list("lease_agency", flat=True)
                    .distinct()
                )
                agency_affiliates = agencies_connected.count()

                # 3️⃣ Lease orders using owner's vehicles
                total_nos_leases = LeaseOrderMaster.objects.filter(
                    vehicle__vehicle_owner=owner
                ).exclude(
                    order_status__order_status_name__in=[
                        "cancelled",
                        "declined",
                        "owner_declined",
                        "rider_declined"
                    ]
                ).count()

                # 4️⃣ Business owner name from User_Master
                business_owner = f"{owner.user_id.first_name} {owner.user_id.last_name}"

                result.append({
                    "id": owner.id,
                    "business_name": owner.business_name,
                    "email": owner.business_Email,
                    "business_owner": business_owner,
                    "phone": owner.phone_number,
                    "nos_of_vehicles": nos_of_vehicles,
                    "agency_affiliates": agency_affiliates,
                    "total_nos_leases": total_nos_leases,
                })

            serializer = VehicleOwnerListSerializer(result, many=True)
            total_pages = paginator.page.paginator.num_pages
            return paginator.get_paginated_response({
                "status": 1,
                "message": "Vehicle owners fetched successfully.",
                "total_pages": total_pages,
                "data": serializer.data
            })


        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong.",
                "error": str(e),
                "data": None
            }, status=200)

class VehicleActionAPI(APIView):
    permission_classes = [IsAuthenticated]
    @swagger_auto_schema(
        operation_summary="Activate or Deactivate a Vehicle",
        operation_description=(
            "Send `action = activate` to activate a vehicle.\n"
            "Send `action = deactivate` to deactivate a vehicle."
        ),
        manual_parameters=[
            openapi.Parameter(
                "id",
                openapi.IN_PATH,
                description="Vehicle ID",
                type=openapi.TYPE_STRING,
                required=True
            )
        ],
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["action"],
            properties={
                "action": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="Action to perform: activate / deactivate"
                )
            }
        ),
        responses={
            200: openapi.Response(
                description="Vehicle status updated",
                examples={
                    "application/json": {
                        "status": 1,
                        "message": "Vehicle activated successfully.",
                        "data": {
                            "vehicle_id": "uuid",
                            "active": True
                        }
                    }
                }
            )
        }
    )
    def patch(self, request, id):
        user = request.user
        if user.user_type.user_type_name != "Admin":
            return Response({'status': 0, 'message': 'Only administrators are authorized to access this resource.', 'data': None},status=status.HTTP_200_OK)        
        try:
            vehicle = Vehicle_Master.objects.get(id=id)
        except Vehicle_Master.DoesNotExist:
            return Response({
                "status": 0,
                "message": "Vehicle not found.",
                "data": None
            }, status=200)

        action = request.data.get("action")

        if not action:
            return Response({
                "status": 0,
                "message": "Action is required.",
                "data": None
            }, status=200)

        action = action.lower()

        if action not in ["activate", "deactivate"]:
            return Response({
                "status": 0,
                "message": "Invalid action. Use 'activate' or 'deactivate'.",
                "data": None
            }, status=200)

        # Update status
        vehicle.active = (action == "activate")
        vehicle.save()

        return Response({
            "status": 1,
            "message": f"Vehicle {action}d successfully.",
            "data": {
                "vehicle_id": str(vehicle.id),
                "active": vehicle.active
            }
        }, status=200)

## Agency Log
class AgencyLogAPI(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request, agency_id=None):
        try:
            user = request.user

            if user.user_type.user_type_name != "Admin":
                return Response({'status': 0, 'message': 'Only administrators are authorized to access this resource.', 'data': None},status=status.HTTP_200_OK)
            if agency_id:
                agency = (
                    Lease_Agency_Master.objects
                    .filter(id=agency_id)
                    .first()
                )

                if not agency:
                    return Response({
                        "status": 0,
                        "message": "Agency not found.",
                        "data": None
                    }, status=200)

                vehicle_links = Vehicle_Agency.objects.filter(
                    lease_agency=agency,
                    status="Active"
                ).select_related("vehicle_master", "vehicle_master__vehicle_owner")

                vehicles = [link.vehicle_master for link in vehicle_links]
                
                paginator = CustomPagination()
                result_page = paginator.paginate_queryset(vehicles, request)

                serializer = AgencyVehicleDetailSerializer(
                    result_page,
                    many=True,
                    context={"request": request}
                )
                total_pages = paginator.page.paginator.num_pages


                return paginator.get_paginated_response({
                    "status": 1,
                    "message": "Agency vehicles fetched successfully.",
                    "total_pages": total_pages,
                    "data": serializer.data
                })

            agencies = (
                Lease_Agency_Master.objects
                .select_related("user_id")
                .order_by("-created_at")
            )

            paginator = CustomPagination()
            result_page = paginator.paginate_queryset(agencies, request)

            serializer = AgencyLogListSerializer(
                result_page,
                many=True,
                context={"request": request}
            )
            total_pages = paginator.page.paginator.num_pages
            return paginator.get_paginated_response({
                "status": 1,
                "message": "Agency logs fetched successfully.",
                "total_pages": total_pages,
                "data": serializer.data
            })

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong while fetching agency logs.",
                "error": str(e),
                "data": None
            }, status=200)

class TransactionLogAPI(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        try:
            user = request.user
            if user.user_type.user_type_name != "Admin":
                return Response({'status': 0, 'message': 'Only administrators are authorized to access this resource.', 'data': None},status=status.HTTP_200_OK)
            transactions = PaymentMaster.objects.select_related(
                "invoice",
                "invoice__lease_order",
                "invoice__lease_order__user",
                "invoice__lease_order__vehicle",
                "invoice__lease_order__agency",
            ).order_by("-created_at")

            paginator = CustomPagination()
            result_page = paginator.paginate_queryset(transactions, request)

            serializer = TransactionLogSerializer(
                result_page,
                many=True,
                context={"request": request}
            )
            total_pages = paginator.page.paginator.num_pages
            return paginator.get_paginated_response({
                "status": 1,
                "message": "Transaction logs fetched successfully.",
                "total_pages": total_pages,
                "data": serializer.data
            })

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong while fetching transaction logs.",
                "error": str(e),
                "data": None
            }, status=200)

### Policy Section

class PolicyListAPI(APIView):
    permission_classes = [IsAuthenticated]
    @swagger_auto_schema(
        operation_summary="Get all policies",
        operation_description="Fetch a list of all policies.",
        responses={
            200: openapi.Response(
                description="Policies fetched",
                examples={
                    "application/json": {
                        "status": 1,
                        "message": "Policies fetched successfully.",
                        "data": [
                            {
                                "id": "uuid",
                                "policy_name": "Refund Policy",
                                "description": "Details about refunds",
                                "created_at": "2025-02-10T10:00:00Z",
                                "updated_at": "2025-02-10T10:00:00Z"
                            }
                        ]
                    }
                }
            )
        }
    )
    def get(self, request):
        try:
            user = request.user
            if user.user_type.user_type_name != "Admin":
                return Response({'status': 0, 'message': 'Only administrators are authorized to access this resource.', 'data': None},status=status.HTTP_200_OK)
            policies = PolicyMaster.objects.all().order_by("-created_at")
            serializer = PolicyMasterSerializer(policies, many=True)

            return Response({
                "status": 1,
                "message": "Policies fetched successfully.",
                "data": serializer.data
            }, status=200)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong while fetching policies.",
                "error": str(e),
                "data": None
            }, status=200)

class PolicyDetailAPI(APIView):
    permission_classes = [IsAuthenticated]
    @swagger_auto_schema(
        operation_summary="Get policy by ID",
        operation_description="Fetch a single policy using its UUID.",
        responses={
            200: openapi.Response(
                description="Policy fetched",
                examples={
                    "application/json": {
                        "status": 1,
                        "message": "Policy fetched successfully.",
                        "data": {
                            "id": "uuid",
                            "policy_name": "Refund Policy",
                            "description": "Description here"
                        }
                    }
                }
            )
        }
    )
    def get(self, request, pk):
        try:
            user = request.user
            if user.user_type.user_type_name != "Admin":
                return Response({'status': 0, 'message': 'Only administrators are authorized to access this resource.', 'data': None},status=status.HTTP_200_OK)
            policy = PolicyMaster.objects.get(pk=pk)
            serializer = PolicyMasterSerializer(policy)

            return Response({
                "status": 1,
                "message": "Policy fetched successfully.",
                "data": serializer.data
            }, status=200)

        except PolicyMaster.DoesNotExist:
            return Response({
                "status": 0,
                "message": "Policy not found.",
                "data": None
            }, status=200)


    @swagger_auto_schema(
        operation_summary="Update policy",
        operation_description="Update any fields of a policy.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                "policy_name": openapi.Schema(type=openapi.TYPE_STRING),
                "description": openapi.Schema(type=openapi.TYPE_STRING),
            }
        ),
        responses={
            200: openapi.Response(
                description="Policy updated",
                examples={
                    "application/json": {
                        "status": 1,
                        "message": "Policy updated successfully.",
                        "data": {
                            "id": "uuid",
                            "policy_name": "Updated Title",
                            "description": "Updated description"
                        }
                    }
                }
            )
        }
    )
    def patch(self, request, pk):
        user = request.user
        if user.user_type.user_type_name != "Admin":
            return Response({'status': 0, 'message': 'Only administrators are authorized to access this resource.', 'data': None},status=status.HTTP_200_OK)
        try:
            policy = PolicyMaster.objects.get(pk=pk)
        except PolicyMaster.DoesNotExist:
            return Response({
                "status": 0,
                "message": "Policy not found.",
                "data": None
            }, status=200)

        serializer = PolicyMasterSerializer(policy, data=request.data, partial=True)
        
        if serializer.is_valid():
            serializer.save()
            return Response({
                "status": 1,
                "message": "Policy updated successfully.",
                "data": serializer.data
            }, status=200)

        return Response({
            "status": 0,
            "message": "Validation error.",
            "errors": serializer.errors,
            "data": None
        }, status=200)
    
### Commision Section

class CommissionListAPI(APIView):
    permission_classes = [IsAuthenticated]
    @swagger_auto_schema(
        operation_description="Get all commissions",
        responses={200: openapi.Response(
            description="List of all commissions",
            examples={
                "application/json": {
                    "status": 1,
                    "message": "Commissions fetched successfully.",
                    "data": [
                        {"id": "uuid", "commission_name": "...", "value": "7.50"}
                    ]
                }
            }
        )}
    )
    def get(self, request):
        user = request.user
        if user.user_type.user_type_name != "Admin":
            return Response({'status': 0, 'message': 'Only administrators are authorized to access this resource.', 'data': None},status=status.HTTP_200_OK)
        commissions = SetCommissionMaster.objects.all().order_by("-created_at")
        serializer = SetCommissionSerializer(commissions, many=True)

        return Response({
            "status": 1,
            "message": "Commissions fetched successfully.",
            "data": serializer.data
        }, status=200)


class CommissionDetailAPI(APIView):
    permission_classes = [IsAuthenticated]
    @swagger_auto_schema(
        operation_description="Get commission by ID",
        responses={200: openapi.Response(description="Commission detail")}
    )
    def get(self, request, pk):
        try:
            user = request.user
            if user.user_type.user_type_name != "Admin":
                return Response({'status': 0, 'message': 'Only administrators are authorized to access this resource.', 'data': None},status=status.HTTP_200_OK)
            commission = SetCommissionMaster.objects.get(pk=pk)
            serializer = SetCommissionSerializer(commission)
            return Response({
                "status": 1,
                "message": "Commission fetched successfully.",
                "data": serializer.data
            }, status=200)
        except SetCommissionMaster.DoesNotExist:
            return Response({
                "status": 0,
                "message": "Commission not found.",
                "data": None
            }, status=200)

    @swagger_auto_schema(
        operation_description="Update commission value",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                "value": openapi.Schema(
                    type=openapi.TYPE_NUMBER,
                    description="Updated commission percentage"
                )
            }
        )
    )
    def patch(self, request, pk):
        user = request.user
        if user.user_type.user_type_name != "Admin":
            return Response({'status': 0, 'message': 'Only administrators are authorized to access this resource.', 'data': None},status=status.HTTP_200_OK)
        try:
            commission = SetCommissionMaster.objects.get(pk=pk)
        except SetCommissionMaster.DoesNotExist:
            return Response({
                "status": 0,
                "message": "Commission not found.",
                "data": None
            }, status=200)

        serializer = SetCommissionSerializer(
            commission, data=request.data, partial=True
        )

        if serializer.is_valid():
            serializer.save()
            return Response({
                "status": 1,
                "message": "Commission updated successfully.",
                "data": serializer.data
            }, status=200)

        return Response({
            "status": 0,
            "message": "Validation error.",
            "errors": serializer.errors,
            "data": None
        }, status=200)

class VehiclePriceMatrixListAPI(APIView):
    permission_classes = [IsAuthenticated]
    @swagger_auto_schema(
        operation_summary="Get all vehicle price matrix records",
        operation_description="Fetch paginated list of vehicle price matrix entries.",
        manual_parameters=[
            openapi.Parameter(
                "page",
                openapi.IN_QUERY,
                description="Page number",
                type=openapi.TYPE_INTEGER
            ),
        ],
        responses={
            200: openapi.Response(
                description="Vehicle price matrix list",
                examples={
                    "application/json": {
                        "status": 1,
                        "message": "Vehicle price matrix fetched successfully.",
                        "data": [
                            {
                                "id": 1,
                                "vehicle_make": "Toyota",
                                "vehicle_model": "Corolla",
                                "vehicle_year": 2022,
                                "lease_per_day": 5000,
                                "delivery_rate_per_km": 20,
                                "micro_insurance_rate_per_rider": 10
                            }
                        ]
                    }
                }
            )
        }
    )
    def get(self, request):
        try:
            user = request.user
            if user.user_type.user_type_name != "Admin":
                return Response({'status': 0, 'message': 'Only administrators are authorized to access this resource.', 'data': None},status=status.HTTP_200_OK)
            queryset = VehiclePriceMatrix.objects.all().order_by("-created_at")

            paginator = CustomPagination()
            paginated_data = paginator.paginate_queryset(queryset, request)

            serializer = VehiclePriceMatrixSerializer(paginated_data, many=True)
            total_pages = paginator.page.paginator.num_pages
            return paginator.get_paginated_response({
                "status": 1,
                "message": "Vehicle price matrix fetched successfully.",
                "total_pages": total_pages,
                "data": serializer.data
            })

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Something went wrong while fetching price matrix.",
                "error": str(e),
                "data": None
            }, status=200)

class VehiclePriceMatrixDetailAPI(APIView):
    permission_classes = [IsAuthenticated]
    @swagger_auto_schema(
        operation_summary="Get vehicle price matrix by ID",
        responses={
            200: openapi.Response(
                description="Vehicle price matrix fetched",
                examples={
                    "application/json": {
                        "status": 1,
                        "message": "Vehicle price matrix fetched successfully.",
                        "data": {
                            "id": "7b672fa4-79a4-4438-bc5b-63b3e18b42ad",
                            "vehicle_make": "Toyota",
                            "vehicle_model": "Corolla",
                            "vehicle_year": 2022,
                            "lease_per_day": 5000
                        }
                    }
                }
            )
        }
    )
    def get(self, request, pk):
        user = request.user
        if user.user_type.user_type_name != "Admin":
            return Response({'status': 0, 'message': 'Only administrators are authorized to access this resource.', 'data': None},status=status.HTTP_200_OK)
        try:
            price = VehiclePriceMatrix.objects.get(pk=pk)
            serializer = VehiclePriceMatrixSerializer(price)

            return Response({
                "status": 1,
                "message": "Vehicle price matrix fetched successfully.",
                "data": serializer.data
            }, status=200)

        except VehiclePriceMatrix.DoesNotExist:
            return Response({
                "status": 0,
                "message": "Vehicle price matrix not found.",
                "data": None
            }, status=200)


    @swagger_auto_schema(
        operation_summary="Update vehicle price matrix",
        operation_description="Partial update of vehicle price matrix fields.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                "lease_per_day": openapi.Schema(type=openapi.TYPE_INTEGER),
                "delivery_rate_per_km": openapi.Schema(type=openapi.TYPE_INTEGER),
                "micro_insurance_rate_per_rider": openapi.Schema(type=openapi.TYPE_INTEGER),
                "vehicle_class": openapi.Schema(type=openapi.TYPE_STRING),
                "vehicle_location": openapi.Schema(type=openapi.TYPE_STRING),
                "vehicle_year": openapi.Schema(type=openapi.TYPE_INTEGER),
            }
        ),
        responses={
            200: openapi.Response(
                description="Vehicle price matrix updated",
                examples={
                    "application/json": {
                        "status": 1,
                        "message": "Vehicle price matrix updated successfully.",
                        "data": {
                            "id": "7b672fa4-79a4-4438-bc5b-63b3e18b42ad",
                            "lease_per_day": 5500
                        }
                    }
                }
            )
        }
    )
    def patch(self, request, pk):
        user = request.user
        if user.user_type.user_type_name != "Admin":
            return Response({'status': 0, 'message': 'Only administrators are authorized to access this resource.', 'data': None},status=status.HTTP_200_OK)
        try:
            price = VehiclePriceMatrix.objects.get(pk=pk)
        except VehiclePriceMatrix.DoesNotExist:
            return Response({
                "status": 0,
                "message": "Vehicle price matrix not found.",
                "data": None
            }, status=200)

        serializer = VehiclePriceMatrixSerializer(price, data=request.data, partial=True)

        if serializer.is_valid():
            serializer.save()
            return Response({
                "status": 1,
                "message": "Vehicle price matrix updated successfully.",
                "data": serializer.data
            }, status=200)

        return Response({
            "status": 0,
            "message": "Validation error.",
            "errors": serializer.errors,
            "data": None
        }, status=200)


class VehicleUpdateAPI(APIView):
    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        security=[{"Bearer": []}],
        operation_summary="Update vehicle details",
        operation_description=(
            "Partially update vehicle details.\n\n"
            "You can update normal fields and/or replace selected images.\n"
            "For image replacement, `replace_image_ids` and `images` count must match."
        ),
        consumes=["multipart/form-data"],
        manual_parameters=[
            openapi.Parameter(
                "vehicle_id",
                openapi.IN_PATH,
                description="Vehicle ID (UUID)",
                type=openapi.TYPE_STRING,
                format="uuid",
                required=True
            ),

            # -------- Normal fields --------
            openapi.Parameter("plate_number", openapi.IN_FORM, type=openapi.TYPE_STRING),
            openapi.Parameter("vehicle_make", openapi.IN_FORM, type=openapi.TYPE_STRING),
            openapi.Parameter("vehicle_model", openapi.IN_FORM, type=openapi.TYPE_STRING),
            openapi.Parameter("body_type", openapi.IN_FORM, type=openapi.TYPE_STRING),
            openapi.Parameter("vehicle_identify_number", openapi.IN_FORM, type=openapi.TYPE_STRING),
            openapi.Parameter("primary_location", openapi.IN_FORM, type=openapi.TYPE_STRING),

            openapi.Parameter(
                "engine_spec",
                openapi.IN_FORM,
                type=openapi.TYPE_STRING,
                description="Engine specifications (text)"
            ),
            openapi.Parameter(
                "other_spec",
                openapi.IN_FORM,
                type=openapi.TYPE_STRING,
                description="Other vehicle specifications (text)"
            ),

            # -------- Numeric fields --------
            openapi.Parameter("mfg_year", openapi.IN_FORM, type=openapi.TYPE_INTEGER),
            openapi.Parameter("lease_price_per_day", openapi.IN_FORM, type=openapi.TYPE_INTEGER),
            openapi.Parameter("passenger_count", openapi.IN_FORM, type=openapi.TYPE_INTEGER),

            # -------- Boolean field --------
            openapi.Parameter("active", openapi.IN_FORM, type=openapi.TYPE_BOOLEAN),

            # -------- Image replace (optional) --------
            openapi.Parameter(
                "replace_image_ids",
                openapi.IN_FORM,
                type=openapi.TYPE_STRING,
                description="UUIDs of images to replace (order matters)",
                multiple=True,
                required=False
            ),
            openapi.Parameter(
                "images",
                openapi.IN_FORM,
                type=openapi.TYPE_FILE,
                description="New image files",
                multiple=True,
                required=False
            ),
        ],
        responses={
            200: openapi.Response(
                description="Vehicle updated successfully",
                examples={
                    "application/json": {
                        "status": 1,
                        "message": "Vehicle updated successfully.",
                        "data": {
                            "vehicle_id": "7b672fa4-79a4-4438-bc5b-63b3e18b42ad",
                            "updated_at": "2025-01-20T12:30:00Z"
                        }
                    }
                }
            )
        }
    )

    def patch(self, request, vehicle_id):
        user = request.user
        if user.user_type.user_type_name != "Admin":
            return Response({'status': 0, 'message': 'Only administrators are authorized to access this resource.', 'data': None},status=status.HTTP_200_OK)
        try:
            vehicle = Vehicle_Master.objects.filter(id=vehicle_id).first()
            if not vehicle:
                return Response({
                    "status": 0,
                    "message": "Vehicle not found.",
                    "data": None
                }, status=200)

            # ----------------------------------
            # IMAGE REPLACEMENT (PARTIAL)
            # ----------------------------------
            replace_image_ids = request.data.getlist("replace_image_ids")
            new_images = request.FILES.getlist("images")

            if replace_image_ids or new_images:
                # Both must be present
                if not replace_image_ids or not new_images:
                    return Response({
                        "status": 0,
                        "message": "Both replace_image_ids and images are required for image update.",
                        "data": None
                    }, status=200)

                if len(replace_image_ids) != len(new_images):
                    return Response({
                        "status": 0,
                        "message": "replace_image_ids count must match images count.",
                        "data": None
                    }, status=200)

                # Validate images first
                for img in new_images:
                    if img.size > 5 * 1024 * 1024:
                        return Response({
                            "status": 0,
                            "message": "Each image must be less than 5MB.",
                            "data": None
                        }, status=200)

                    if not img.content_type.startswith("image/"):
                        return Response({
                            "status": 0,
                            "message": "Only image files are allowed.",
                            "data": None
                        }, status=200)

                # Replace images one by one
                for old_id, new_file in zip(replace_image_ids, new_images):
                    img_obj = Vehicle_Image.objects.filter(
                        id=old_id,
                        vehicle_master=vehicle
                    ).first()

                    if not img_obj:
                        continue  # skip invalid image id

                    img_obj.image = new_file
                    img_obj.save()

            def parse_bool(value):
                if isinstance(value, bool):
                    return value
                if isinstance(value, str):
                    return value.lower() in ["true", "1", "yes"]
                return False

            def parse_int(value):
                try:
                    return int(value)
                except:
                    return None


            # ----------------------------------
            # NORMAL FIELD UPDATE (PARTIAL)
            # ----------------------------------

            string_fields = [
                "plate_number",
                "vehicle_make",
                "vehicle_model",
                "body_type",
                "vehicle_identify_number",
                "primary_location",
                "engine_spec",
                "other_spec",
            ]

            int_fields = [
                "mfg_year",
                "lease_price_per_day",
                "passenger_count",
            ]

            bool_fields = [
                "active",
            ]

            # Update string fields
            for field in string_fields:
                if field in request.data:
                    setattr(vehicle, field, request.data.get(field))

            # Update integer fields safely
            for field in int_fields:
                if field in request.data:
                    value = parse_int(request.data.get(field))
                    setattr(vehicle, field, value)

            # Update boolean fields safely
            for field in bool_fields:
                if field in request.data:
                    value = parse_bool(request.data.get(field))
                    setattr(vehicle, field, value)

            vehicle.save()

            return Response({
                "status": 1,
                "message": "Vehicle updated successfully.",
                "data": {
                    "vehicle_id": str(vehicle.id),
                    "updated_at": vehicle.updated_at
                }
            }, status=200)

        except Exception as e:
            return Response({
                "status": 0,
                "message": "Error updating vehicle.",
                "error": str(e),
                "data": None
            }, status=200)


class LogoutAPI(APIView):
    def post(self, request):
        try:
            session_key = request.headers.get('session-key')
            print("Received session-key:", session_key)

            if not session_key:
                return Response({"status": 0, "message": "Session key not found!", "data": None}, status=status.HTTP_200_OK)

            # Delete custom session
            try:
                session = CustomSession.objects.get(session_key=session_key)
                print("Found CustomeSession:", session)
                session.delete()
            except CustomSession.DoesNotExist:
                print("CustomeSession does not exist. Proceeding as already logged out.")

            # Delete Django session 
            try:
                default_session = SessionStore(session_key=session_key)
                default_session.delete()
                print("Default Django session deleted successfully.")
            except Exception as e:
                print(f"Error deleting default session: {e}")

           
            logout(request)

            return Response({'status': 1, 'message': 'Logout successful', "data": None}, status=status.HTTP_200_OK)

        except Exception as e:
            print(f"Full Exception: {e}")
            return Response({'status': 0, 'message': 'Logout failed', 'data': str(e)}, status=status.HTTP_200_OK)
        
        
        
        
