from collections import defaultdict
import re
from django.conf import settings
from rest_framework import serializers
from .models import *
import base64, requests
from django.core.files.base import ContentFile
import os

class NINResponseSerializer(serializers.Serializer):
    nin_number = serializers.CharField(required=False)
    first_name = serializers.CharField(required=False)
    middle_name = serializers.CharField(required=False)
    last_name = serializers.CharField(required=False)
    date_of_birth = serializers.CharField(required=False)
    phone_number = serializers.CharField(required=False)
    gender = serializers.CharField(required=False)
    customer = serializers.CharField(required=False)
    photo = serializers.SerializerMethodField()

    def get_photo(self, obj):
        nin_image_data = obj.get("photo")

        if nin_image_data and nin_image_data.startswith(("/9j/", "iVBOR", "R0lGOD")):
            # Determine file extension
            if nin_image_data.startswith("/9j/"):
                ext = "jpg"
            elif nin_image_data.startswith("iVBOR"):
                ext = "png"
            elif nin_image_data.startswith("R0lGOD"):
                ext = "gif"
            else:
                ext = "jpg"

            file_name = f"nin_{uuid.uuid4()}.{ext}"
            file_path = os.path.join("users/photo", file_name)

            # Save Base64 image
            full_path = os.path.join(settings.MEDIA_ROOT, file_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(base64.b64decode(nin_image_data))

            # Return absolute URL
            request = self.context.get("request")
            return request.build_absolute_uri(f"{settings.MEDIA_URL}{file_path}")

        return nin_image_data



class UserSerializers(serializers.ModelSerializer):
    photo = serializers.CharField(required=False, allow_null=True)  # accept URL or path string

    class Meta:
        model = User_Master
        fields = [
            "photo", "nin_number", "first_name", "last_name", "email",
            "gender", "middle_name", "date_of_birth", "phone_number",
            "employment_status", "marital_status"
        ]
        extra_kwargs = {
            "email": {"required": False},
            "first_name": {"required": False},
            "last_name": {"required": False},
        }

    def validate_date_of_birth(self, value):
        if isinstance(value, str):
            try:
                return datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError:
                raise serializers.ValidationError("Invalid date format for date_of_birth.")
        return value

    def create(self, validated_data):

        photo_data = validated_data.pop("photo", None)
        user = User_Master.objects.create(**validated_data)
        # Case 1: NIN photo path (already saved in MEDIA)
        if isinstance(photo_data, str) and photo_data.startswith("users/"):
            user.photo.name = photo_data
            user.save()

        # Case 2: NIN absolute URL (convert to relative)
        elif isinstance(photo_data, str) and "/media/users/" in photo_data:
            relative_path = photo_data.split("/media/")[-1]
            user.photo.name = relative_path
            user.save()

        return user

    def to_representation(self, instance):
        """Return full URL for photo"""
        data = super().to_representation(instance)
        request = self.context.get("request")
        if instance.photo and hasattr(instance.photo, "url"):
            if request:
                data["photo"] = request.build_absolute_uri(instance.photo.url)
            else:
                data["photo"] = instance.photo.url
        else:
            data["photo"] = None
        return data

    
class ResetPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    new_password = serializers.CharField(write_only=True, required=True)
    confirm_password = serializers.CharField(write_only=True, required=True)

    def validate(self, attrs):
        new_password = attrs.get("new_password")
        confirm_password = attrs.get("confirm_password")

        if new_password != confirm_password:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})

        # Add your existing password validation logic
        if len(new_password) < 8:
            raise serializers.ValidationError({"new_password": "Password must be at least 8 characters long."})
        if not re.search(r"[A-Z]", new_password):
            raise serializers.ValidationError({"new_password": "Password must include at least one uppercase letter."})
        if not re.search(r"\d", new_password):
            raise serializers.ValidationError({"new_password": "Password must contain at least one number."})
        if not re.search(r"[!@#$_%^&*(),.?\":{}|<>]", new_password):
            raise serializers.ValidationError({"new_password": "Password must contain at least one special character."})

        return attrs


class UserTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = User_Type
        fields = ['user_type_name']


class VehicleImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vehicle_Image
        fields = ["id","vehicle_master","image","uploaded_at"]
        
    
class VehicleSerializer(serializers.ModelSerializer):
    images = serializers.SerializerMethodField()

    class Meta:
        model = Vehicle_Master
        fields = [
            "id",
            "images",
            "vehicle_owner",
            "registered_owner",
            "plate_number",
            "vehicle_make",
            "vehicle_model",
            "body_type",
            "mfg_year",
            "vehicle_identify_number",
            "license_renewed_date",
            "license_expiry_date",
            "insurance_renewed_date",
            "insurance_expiry_date",
            "road_worthiness_cert_date",
            "road_worthiness_expiry_date",
            "engine_spec",
            "other_spec",
            "primary_location",
            "lease_price_per_day",
            "vehicle_status",
            "created_at",
            "updated_at"
        ]

    def get_images(self, obj):
        images = Vehicle_Image.objects.filter(vehicle_master=obj)
        request = self.context.get("request")

        result = []
        for img in images:
            if img.image:
                if request:
                    image_url = request.build_absolute_uri(img.image.url)
                else:
                    image_url = f"{settings.MEDIA_URL}{img.image.name}"
                result.append({"image": image_url})
        return result


    def validate(self, data):
        if data.get("lease_price_per_day") is not None and data["lease_price_per_day"] < 0:
            raise serializers.ValidationError("Lease price per day cannot be negative.")
        return data
    
class VehicleStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = VehicleStatusMaster
        fields = ["vehicle_status_id", "vehicle_status_name", "description", "is_active"]

class OrderVehicleLease(serializers.ModelSerializer):
    images = serializers.SerializerMethodField()
    vehicle_status = VehicleStatusSerializer(read_only=True)

    class Meta:
        model = Vehicle_Master
        fields = [
            "id",
            "images",
            "vehicle_owner",
            "registered_owner",
            "plate_number",
            "vehicle_make",
            "vehicle_model",
            "body_type",
            "active",
            "passenger_count",
            "mfg_year",
            "lease_price_per_day",
            "vehicle_status",
            "created_at",
            "updated_at"
        ]

    def get_images(self, obj):
        images = Vehicle_Image.objects.filter(vehicle_master=obj)
        request = self.context.get("request")

        result = []
        for img in images:
            if img.image:
                if request:
                    image_url = request.build_absolute_uri(img.image.url)
                else:
                    image_url = f"{settings.MEDIA_URL}{img.image.name}"
                result.append({"image": image_url})
        return result


    def validate(self, data):
        if data.get("lease_price_per_day") is not None and data["lease_price_per_day"] < 0:
            raise serializers.ValidationError("Lease price per day cannot be negative.")
        return data

        

    
    
class ContactInfoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContactInfo
        fields = ["id", "owner_name", "email", "phone_number"]


class LeaseAgencySerializer(serializers.ModelSerializer):
    contact_infos = ContactInfoSerializer(many=True, read_only=True, source="user_id.contact_infos")
    agency_profile = serializers.SerializerMethodField() 

    class Meta:
        model = Lease_Agency_Master
        fields = [
            "id", "user_id", "cac_number", "full_name", "business_name", "business_Email",
            "business_number", "business_type", "phone_number", "year", "state", "company_name",
            "address","agency_profile",  
            "contact_infos"
        ]

    def get_agency_profile(self, obj):
        """Return only the image URL, not base64."""
        request = self.context.get("request")
        if obj.agency_profile:
            return request.build_absolute_uri(obj.agency_profile.url)
        return None
    
class GetLeaseAgencySerializer(serializers.ModelSerializer):
    contact_infos = ContactInfoSerializer(
        many=True,
        read_only=True,
        source="user_id.contact_infos"
    )
    agency_profile = serializers.SerializerMethodField()

    class Meta:
        model = Lease_Agency_Master
        fields = [
            "id",
            "user_id",
            "cac_number",
            "full_name",
            "business_name",
            "business_Email",
            "business_number",
            "business_type",
            "phone_number",
            "year",
            "state",
            "company_name",
            "address",
            "agency_profile",
            "contact_infos",
        ]

    def get_agency_profile(self, obj):
        """Return the full absolute URL for agency_profile image."""
        request = self.context.get("request")
        if obj.agency_profile:
            try:
                url = obj.agency_profile.url
                if request:
                    return request.build_absolute_uri(url)
                return url  # fallback to relative URL if no request context
            except Exception as e:
                print("⚠️ URL fetch failed:", e)
        return None


class UserBasicSerializer(serializers.ModelSerializer):
    class Meta:
        model = User_Master
        fields = ["first_name", "last_name"]



class OrderStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderStatusMaster   
        fields = ["id", "order_status_name", "description"]


class UserBasicSerializer(serializers.ModelSerializer):
    class Meta:
        model = User_Master
        fields = [
            "first_name", "last_name"
        ]


class OrderSerializer(serializers.ModelSerializer):
    order_status = OrderStatusSerializer(read_only=True)
    vehicle = OrderVehicleLease(read_only=True)
    agency = LeaseAgencySerializer(read_only=True)
    offered_by = serializers.SerializerMethodField()  
    user = UserBasicSerializer(read_only=True)

    class Meta:
        model = LeaseOrderMaster
        fields = [
            "lease_order_id",
            "order_number",
            "user",
            "vehicle",
            "agency",
            "order_status",
            "purpose",
            "state",
            "lease_type",
            "leased_for",
            "start_date",
            "end_date",
            "total_days",
            "client_location",
            "delivery_address",
            "delivery_distance_km",
            "estimated_delivery_cost",
            "total_amount",
            "driver",
            "offered_by",  #  include this
            "no_of_passenger",
            "created_at",
            "updated_at"
        ]

    def get_offered_by(self, obj):
        """Return only the business name of the vehicle owner"""
        if obj.vehicle and hasattr(obj.vehicle, "vehicle_owner") and obj.vehicle.vehicle_owner:
            return {"business_name": obj.vehicle.vehicle_owner.business_name}
        return None

    def get_total_amount(self, obj):
        if obj.total_amount:
            return obj.total_amount
        elif obj.vehicle and obj.vehicle.lease_price_per_day and obj.total_days:
            return round((obj.vehicle.lease_price_per_day * obj.total_days) + (obj.estimated_delivery_cost or 0), 2)
        return None
    
class OrderDetailsSerializer(serializers.ModelSerializer):
    # order_number = serializers.SerializerMethodField()
    vehicle_name = serializers.SerializerMethodField()
    vehicle_category = serializers.SerializerMethodField()
    vehicle_id_number = serializers.SerializerMethodField()
    offered_by = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()  
    lease_period_start = serializers.DateTimeField(source='start_date', format='%d %b %H:%M')
    lease_period_end = serializers.DateTimeField(source='end_date', format='%d %b %H:%M')
    duration_days = serializers.IntegerField(source='total_days', read_only=True)
    lease_type = serializers.CharField()
    leased_for = serializers.CharField()
    purpose = serializers.CharField()
    client_location = serializers.CharField()
    delivery_address = serializers.CharField()
    delivery_distance_km = serializers.FloatField()
    estimated_delivery_cost = serializers.FloatField()
    total_amount = serializers.FloatField()

    class Meta:
        model = LeaseOrderMaster
        fields = [
            # "order_number",
            "vehicle_name",
            "vehicle_category",
            "vehicle_id_number",
            "offered_by",
            "status",  
            "lease_period_start",
            "lease_period_end",
            "duration_days",
            "lease_type",
            "leased_for",
            "purpose",
            "client_location",
            "delivery_address",
            "delivery_distance_km",
            "estimated_delivery_cost",
            "total_amount",
        ]

    # === Custom Field Getters ===
    # def get_order_number(self, obj):
    #     return f"{str(obj.id)[:6].upper()}" if obj.id else None

    def get_vehicle_name(self, obj):
        if obj.vehicle:
            return f"{obj.vehicle.vehicle_make} {obj.vehicle.vehicle_model}, {obj.vehicle.mfg_year}"
        return None

    def get_vehicle_category(self, obj):
        if obj.vehicle and hasattr(obj.vehicle, "vehicle_category"):
            return obj.vehicle.vehicle_category
        return None

    def get_vehicle_id_number(self, obj):
        """Return vehicle registration number"""
        if obj.vehicle:
            return getattr(obj.vehicle, "plate_number", None) 
        return None


    def get_offered_by(self, obj):
        if obj.agency:
            return obj.agency.business_name
        elif obj.vehicle and obj.vehicle.vehicle_owner:
            return obj.vehicle.vehicle_owner.business_name
        return None

    def get_status(self, obj):
        """Return readable order status name"""
        if obj.order_status:
            return obj.order_status.order_status_name
        return None

        
class VehicleOwnerSerializer(serializers.ModelSerializer):
    vehicles = serializers.SerializerMethodField()

    class Meta:
        model = Vehicle_Owner_Master
        fields = ["id", "full_name", "business_name", "business_Email", "business_number", "phone_number", "year", "state", "vehicles","address"]

    def get_vehicles(self, obj):
        vehicles = Vehicle_Master.objects.filter(vehicle_owner=obj)
        return VehicleSerializer(vehicles, many=True, context=self.context).data


class ReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaseReviewMaster
        fields = "__all__"


class InvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaseInvoice
        fields = "__all__"


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentMaster
        fields = "__all__"

        
# class DashboardSerializer(serializers.ModelSerializer):
#     user_type = UserTypeSerializer(read_only=True)
#     photo = serializers.SerializerMethodField()
#     contact_infos = ContactInfoSerializer(many=True, read_only=True)
#     lease_agencies = serializers.SerializerMethodField()
#     business_info = serializers.SerializerMethodField()
#     vehicles = serializers.SerializerMethodField()
#     lease_orders = serializers.SerializerMethodField()
#     reviews = serializers.SerializerMethodField()
#     invoices = serializers.SerializerMethodField()
#     payments = serializers.SerializerMethodField()

#     class Meta:
#         model = User_Master
#         fields = [
#             "id", "first_name", "last_name","middle_name", "email", "phone_number", "photo"
#             , "user_type",
#             "business_info", "contact_infos", "lease_agencies", "vehicles",
#             "lease_orders", "reviews", "invoices", "payments"
#         ]

#     # Business Info (from Vehicle_Owner_Master)
#     def get_business_info(self, obj):
#         owners = Vehicle_Owner_Master.objects.filter(user_id=obj)
#         business_list = []
#         for owner in owners:
#             business_list.append({
#                 "id": str(owner.id),
#                 "full_name": owner.full_name,
#                 "business_name": owner.business_name,
#                 "business_Email": owner.business_Email,
#                 "business_number": owner.business_number,
#                 "phone_number": owner.phone_number,
#                 "year": owner.year,
#                 "state": owner.state,
#                 "address":owner.address,
#             })
#         return business_list
#     def get_photo(self, obj):
#         request = self.context.get("request")
#         if obj.photo:
#             return request.build_absolute_uri(obj.photo.url) if request else f"{settings.MEDIA_URL}{obj.photo.name}"
#         return None


#     def get_vehicles(self, obj):
#         vehicles = Vehicle_Master.objects.filter(vehicle_owner__user_id=obj).prefetch_related("images")
#         vehicle_list = []

#         request = self.context.get("request")

#         for v in vehicles:
#             image_urls = []
#             for img in v.images.all():
#                 if img.image:
#                     image_url = request.build_absolute_uri(img.image.url) if request else f"{settings.MEDIA_URL}{img.image.name}"
#                     image_urls.append({"image": image_url})

#             vehicle_list.append({
#                 "id": str(v.id),
#                 "images": image_urls,
#                 "vehicle_owner": str(v.vehicle_owner_id),
#                 "registration_number": v.registration_number,
#                 "plate_number": v.plate_number,
#                 "vehicle_make": v.vehicle_make,
#                 "vehicle_model": v.vehicle_model,
#                 "body_type": v.body_type,
#                 "mfg_year": v.mfg_year,
#                 "vehicle_identify_number": v.vehicle_identify_number,
#                 "license_renewed_date": v.license_renewed_date,
#                 "license_expiry_date": v.license_expiry_date,
#                 "insurance_renewed_date": v.insurance_renewed_date,
#                 "insurance_expiry_date": v.insurance_expiry_date,
#                 "road_worthiness_cert_date": v.road_worthiness_cert_date,
#                 "road_worthiness_expiry_date": v.road_worthiness_expiry_date,
#                 "engine_spec": v.engine_spec,
#                 "other_spec": v.other_spec,
#                 "primary_location": v.primary_location,
#                 "lease_price_per_day": v.lease_price_per_day,
#                 "status": v.status,
#                 "created_at": v.created_at,
#                 "updated_at": v.updated_at,
#             })

#         return vehicle_list


#     def get_lease_agencies(self, obj):
#         owner_ids = Vehicle_Owner_Master.objects.filter(user_id=obj).values_list("id", flat=True)
#         vehicle_agency_links = Vehicle_Agency.objects.filter(
#             vehicle_master__vehicle_owner_id__in=owner_ids
#         ).select_related("lease_agency", "vehicle_master")

#         grouped_data = defaultdict(lambda: {"agency_id": None, "agency_name": None, "vehicles": []})

#         for link in vehicle_agency_links:
#             agency = link.lease_agency
#             vehicle = link.vehicle_master

#             if grouped_data[agency.id]["agency_id"] is None:
#                 grouped_data[agency.id]["agency_id"] = str(agency.id)
#                 grouped_data[agency.id]["agency_name"] = agency.business_name

#             grouped_data[agency.id]["vehicles"].append({
#                 "vehicle_id": str(vehicle.id),
#                 "plate_number": vehicle.plate_number,
#                 "vehicle_make": vehicle.vehicle_make,
#                 "vehicle_model": vehicle.vehicle_model,
#                 "status": vehicle.status,
#             })

#         return list(grouped_data.values())

#     #  Lease Orders
#     def get_lease_orders(self, obj):
#         orders = LeaseOrderMaster.objects.filter(user_id=obj)
#         return OrderSerializer(orders, many=True, context=self.context).data

#     #  Reviews
#     def get_reviews(self, obj):
#         reviews = LeaseReviewMaster.objects.filter(user_id=obj)
#         return ReviewSerializer(reviews, many=True, context=self.context).data

#     # Invoices
#     def get_invoices(self, obj):
#         invoices = LeaseInvoice.objects.filter(lease_order__user_id=obj)
#         return InvoiceSerializer(invoices, many=True, context=self.context).data

#     # Payments
#     def get_payments(self, obj):
#         payments = PaymentMaster.objects.filter(invoice__lease_order__user_id=obj)
#         return PaymentSerializer(payments, many=True, context=self.context).data

class DashboardSerializer(serializers.ModelSerializer):
    photo = serializers.SerializerMethodField()
    lease_agencies = serializers.SerializerMethodField()
    business_info = serializers.SerializerMethodField()
    vehicles = serializers.SerializerMethodField()
    lease_orders = serializers.SerializerMethodField()


    class Meta:
        model = User_Master
        fields = [
            "id", "first_name", "last_name" , "photo",
            "business_info", "lease_agencies", "vehicles",
            "lease_orders", 
        ]

    # Business Info (from Vehicle_Owner_Master)
    def get_business_info(self, obj):
        owners = Vehicle_Owner_Master.objects.filter(user_id=obj)
        business_list = []
        for owner in owners:
            business_list.append({
                "id": str(owner.id),
                "full_name": owner.full_name,
                "business_name": owner.business_name,
                "business_Email": owner.business_Email,
                "business_number": owner.business_number,
                "phone_number": owner.phone_number,
                "year": owner.year,
                "state": owner.state,
                "address":owner.address,
            })
        return business_list
    def get_photo(self, obj):
        request = self.context.get("request")
        if obj.photo:
            return request.build_absolute_uri(obj.photo.url) if request else f"{settings.MEDIA_URL}{obj.photo.name}"
        return None


    def get_vehicles(self, obj):
        vehicles = Vehicle_Master.objects.filter(vehicle_owner__user_id=obj).prefetch_related("images")
        vehicle_list = []

        request = self.context.get("request")

        for v in vehicles:
            image_urls = []
            for img in v.images.all():
                if img.image:
                    image_url = request.build_absolute_uri(img.image.url) if request else f"{settings.MEDIA_URL}{img.image.name}"
                    image_urls.append({"image": image_url})

            vehicle_list.append({
                "id": str(v.id),
                "images": image_urls,
                "vehicle_owner": str(v.vehicle_owner_id),
                "registered_owner": v.registered_owner,
                "plate_number": v.plate_number,
                "vehicle_make": v.vehicle_make,
                "vehicle_model": v.vehicle_model,
                "body_type": v.body_type,
                "mfg_year": v.mfg_year,
                "vehicle_identify_number": v.vehicle_identify_number,
                "license_renewed_date": v.license_renewed_date,
                "license_expiry_date": v.license_expiry_date,
                "insurance_renewed_date": v.insurance_renewed_date,
                "insurance_expiry_date": v.insurance_expiry_date,
                "road_worthiness_cert_date": v.road_worthiness_cert_date,
                "road_worthiness_expiry_date": v.road_worthiness_expiry_date,
                "engine_spec": v.engine_spec,
                "other_spec": v.other_spec,
                "primary_location": v.primary_location,
                "lease_price_per_day": v.lease_price_per_day,
                "status": v.status,
                "created_at": v.created_at,
                "updated_at": v.updated_at,
            })

        return vehicle_list


    def get_lease_agencies(self, obj):
        owner_ids = Vehicle_Owner_Master.objects.filter(user_id=obj).values_list("id", flat=True)
        vehicle_agency_links = Vehicle_Agency.objects.filter(
            vehicle_master__vehicle_owner_id__in=owner_ids
        ).select_related("lease_agency", "vehicle_master")

        grouped_data = defaultdict(lambda: {"agency_id": None, "agency_name": None, "vehicles": []})

        for link in vehicle_agency_links:
            agency = link.lease_agency
            vehicle = link.vehicle_master

            if grouped_data[agency.id]["agency_id"] is None:
                grouped_data[agency.id]["agency_id"] = str(agency.id)
                grouped_data[agency.id]["agency_name"] = agency.business_name

            grouped_data[agency.id]["vehicles"].append({
                "vehicle_id": str(vehicle.id),
                "plate_number": vehicle.plate_number,
                "vehicle_make": vehicle.vehicle_make,
                "vehicle_model": vehicle.vehicle_model,
                "status": vehicle.status,
            })

        return list(grouped_data.values())

    #  Lease Orders
    def get_lease_orders(self, obj):
        orders = LeaseOrderMaster.objects.filter(user_id=obj)
        return OrderSerializer(orders, many=True, context=self.context).data

    #  Reviews
    def get_reviews(self, obj):
        reviews = LeaseReviewMaster.objects.filter(user_id=obj)
        return ReviewSerializer(reviews, many=True, context=self.context).data

    # Invoices
    def get_invoices(self, obj):
        invoices = LeaseInvoice.objects.filter(lease_order__user_id=obj)
        return InvoiceSerializer(invoices, many=True, context=self.context).data

    # Payments
    def get_payments(self, obj):
        payments = PaymentMaster.objects.filter(invoice__lease_order__user_id=obj)
        return PaymentSerializer(payments, many=True, context=self.context).data

class LeaseInvoiceSerializer(serializers.ModelSerializer):
    customer_name = serializers.SerializerMethodField()
    invoice_status_name = serializers.SerializerMethodField()

    class Meta:
        model = LeaseInvoice
        fields = "__all__"
        read_only_fields = ("invoice_id", "invoice_number", "created_at")

    def get_customer_name(self, obj):
        user = obj.lease_order.user
        return f"{user.first_name} {user.last_name}".strip()

    def get_invoice_status_name(self, obj):
        return obj.invoice_status.invoice_status_name

class AllLeaseInvoiceSerializer(serializers.ModelSerializer):
    customer_name = serializers.SerializerMethodField()
    invoice_status_name = serializers.SerializerMethodField()

    class Meta:
        model = LeaseInvoice
        fields = "__all__"
        read_only_fields = ("invoice_id", "invoice_number", "created_at")

    def get_customer_name(self, obj):
        user = obj.lease_order.user
        return f"{user.first_name} {user.last_name}".strip()

    def get_invoice_status_name(self, obj):
        return obj.invoice_status.invoice_status_name

class RiderOrderSerializer(serializers.ModelSerializer):
    vehicle = serializers.SerializerMethodField()
    order_status = serializers.CharField(source="order_status.order_status_name")
    agency = serializers.SerializerMethodField()

    class Meta:
        model = LeaseOrderMaster
        fields = [
            "lease_order_id",
            "agency",
            "order_number",
            "start_date",
            "end_date",
            "total_amount",
            "order_status",
            "vehicle",
            "created_at",
            "updated_at",
            "remaining_time"
        ]

    def get_vehicle(self, obj):
        request = self.context.get("request")
        vehicle = obj.vehicle

        images = []
        for img in vehicle.images.all():
            images.append(
                request.build_absolute_uri(img.image.url)
                if request else img.image.url
            )

        return {
            "vehicle_id": str(vehicle.id),
            "plate_number": vehicle.plate_number,
            "make": vehicle.vehicle_make,
            "model": vehicle.vehicle_model,
            "mfg_year": vehicle.mfg_year,
            "images": images,
        }
    def get_agency(self, obj):
        request = self.context.get("request")
        agency = obj.agency
        return {
            "agency_profile": request.build_absolute_uri(agency.agency_profile.url) if getattr(agency, "agency_profile", None) else None,
            "agency_phone_number": agency.phone_number
        }
