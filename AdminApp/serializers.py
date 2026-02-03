from rest_framework import serializers
from .models import*
import re


class TempUserSerializer(serializers.ModelSerializer):
    confirm_password = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = AdminTempUser
        fields = ['id','first_name','phone_number','email','password','confirm_password','created_at']
        extra_kwargs = {
            'password': {'write_only': True}
        }

    def validate_email(self, value):

        match = re.search(r'@([a-z]+)\.([a-z]{2,3})$', value)

        if not match:
            raise serializers.ValidationError({'status': 0,"message": "Invalid email format.","data": None})

        domain_name = match.group(1)  # Extract domain name (e.g., "gmail" from "gmail.com")
        domain_extension = match.group(2)  # Extract extension (e.g., "com" from "gmail.com")

        if len(domain_name) < 3 or any(char.isdigit() for char in domain_name):
            raise serializers.ValidationError("Invalid email format.")

        if not (2 <= len(domain_extension) <= 3):
            raise serializers.ValidationError("Invalid email format")

        return value
    
    # def validate_mobile_number(self, value):
    #     value = str(value)
        
    #     if not value.isdigit():
    #         raise serializers.ValidationError({
    #             'status': 0,
    #             'message': "Mobile number must contain only digits.",
    #             'data': None
    #         })
        
    #     if not re.match(r'^\+?[1-9]\d{9,14}$', value):  # Regex for valid mobile numbers
    #         raise serializers.ValidationError({
    #             'status': 0,
    #             'message': "Invalid mobile number format. Use digits only (e.g., +14155552671 or 919876543210).",
    #             'data': None
    #         })
        
    #     return value
    
    # validate password and hased password 
    def validate(self, data):
            if data['password']!= data['confirm_password']:
                 raise serializers.ValidationError({"password":"password do not match"})
            return data

    def validate_password(self,value):
        if len(value) < 8:
            raise serializers.ValidationError({'status':0, "error": "InvalidPasswordFormat",
                                                "message": "Password must be at least 8 characters long.","data":(None)})
        if not re.search(r'[A-Z]',value):
            raise serializers.ValidationError({'status':0,"error": "InvalidPasswordFormat", 
                                               "message": "Password must include at least one uppercase letter.","data":(None)})
        if not re.search(r'\d',value):
            raise serializers.ValidationError({'status':0,"error": "InvalidPasswordFormat", 
                                              "message":"password must contain at least one numeric charecter","data":(None)})
        if not re.search(r'[!@#$_%^&*(),.?":{}|<>]', value):  
            raise serializers.ValidationError({
            'status':0,
            "error": "InvalidPasswordFormat",
            "message": "Password must contain at least one special character.",
            "data":(None)
        })
        return value
            
    def create(self, validated_data):
         validated_data.pop("confirm_password")
        #  validated_data['password'] = make_password(validated_data['password'])
         return super().create(validated_data)


class OTPVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField(max_length=6)


class RegistrationSerializer(serializers.ModelSerializer):
    profile_photo = serializers.ImageField(use_url=True, allow_null=True, required=False)
    
    class Meta:
        model = User_Master
        fields = ['id','photo','first_name','email','phone_number','created_at']


class GetRegistrationSerializer(serializers.ModelSerializer):
    profile_photo = serializers.ImageField(use_url=True, allow_null=True, required=False)
    
    class Meta:
        model = User_Master
        fields = ['id','photo','first_name','email','phone_number','created_at']

class LeaseOrderDetailSerializer(serializers.ModelSerializer):
    vehicle_spec = serializers.SerializerMethodField()
    vehicle_owner_name = serializers.CharField(source='vehicle.vehicle_owner.full_name', read_only=True)
    vehicle_plate_number = serializers.CharField(source='vehicle.plate_number', read_only=True)
    leasing_agency = serializers.CharField(source='agency.business_name', read_only=True)
    client = serializers.SerializerMethodField()
    status = serializers.CharField(source='order_status.order_status_name', read_only=True)
    driver_name = serializers.SerializerMethodField()
 
    class Meta:
        model = LeaseOrderMaster
        fields = [
            "vehicle_spec",
            "vehicle_owner_name",
            "leasing_agency",
            "client",
            "purpose",
            "start_date",
            "end_date",
            "total_amount",
            "total_days",
            "vehicle_plate_number",
            "client_location",
            "lease_type",
            "created_at",
            "status",
            "driver_name"
        ]
    def get_client(self, obj):
        return f"{obj.user.first_name} {obj.user.last_name}"
    def get_driver_name(self, obj):
        if obj.driver:
            return obj.driver.name
        return "Unspecified"
    def get_vehicle_spec(self, obj):
        return f"{obj.vehicle.vehicle_make} {obj.vehicle.vehicle_model},{obj.vehicle.mfg_year}"

class LeaseOrderLogSerializer(serializers.ModelSerializer):
    vehicle_spec = serializers.SerializerMethodField()
    order = serializers.CharField(source='order_number', read_only=True)
    vehicle_year = serializers.CharField(source='vehicle.mfg_year', read_only=True)
    vehicle_type = serializers.CharField(source='vehicle.body_type', read_only=True)
    leasing_agent = serializers.CharField(source='agency.business_name', read_only=True)
    client = serializers.SerializerMethodField()
    lease_fee = serializers.IntegerField(source='total_amount', read_only=True)
    duration = serializers.IntegerField(source='total_days', read_only=True)
    status = serializers.CharField(source='order_status.order_status_name', read_only=True)
 
    class Meta:
        model = LeaseOrderMaster
        fields = [
            "order",
            "vehicle_type",
            "vehicle_spec",
            "vehicle_year",
            "leasing_agent",
            "client",
            "updated_at",
            "lease_fee",
            "duration",
            "status"
        ]
 
    def get_client(self, obj):
        return f"{obj.user.first_name} {obj.user.last_name}"
    def get_vehicle_spec(self, obj):
        return f"{obj.vehicle.vehicle_make} {obj.vehicle.vehicle_model},{obj.vehicle.mfg_year}"

### Global Inventory

class GlobalVehicleInventorySerializer(serializers.ModelSerializer):
    vehicle_brand = serializers.SerializerMethodField()
    owner_telephone = serializers.CharField(source="vehicle_owner.phone_number")
    status = serializers.CharField(source="vehicle_status.vehicle_status_name")
    
    class Meta:
        model = Vehicle_Master
        fields = [
            "id",
            "vehicle_brand",
            "engine_spec",
            "other_spec",
            "mfg_year",
            "body_type",
            "vehicle_make",
            "vehicle_model",
            "vehicle_identify_number",
            "plate_number",
            "owner_telephone",
            "active",
            "status",
            "primary_location",
            "lease_price_per_day",
            "passenger_count",
            "created_at",
            "updated_at"
        ]
    
    def get_vehicle_brand(self, obj):
        return f"{obj.vehicle_make} {obj.vehicle_model}"
 
class VehicleImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vehicle_Image
        fields = ["id", "image",]
 
class VehicleDetailSerializer(serializers.ModelSerializer):
    vehicle_brand = serializers.SerializerMethodField()
    vehicle_owner_telephone = serializers.CharField(source="vehicle_owner.phone_number")
    owner_name = serializers.CharField(source="vehicle_owner.full_name")
    images = serializers.SerializerMethodField()

    class Meta:
        model = Vehicle_Master
        fields = [
            "id",
            "vehicle_brand",
            "vehicle_identify_number",
            "vehicle_make",
            "vehicle_model",
            "body_type",
            "engine_spec",
            "other_spec",
            "mfg_year",
            "body_type",
            "plate_number",
            "vehicle_owner_telephone",
            "images",
            "owner_name"
        ]

    def get_vehicle_brand(self, obj):
        return f"{obj.vehicle_make} {obj.vehicle_model}"

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
                result.append({"id": img.id,"image": image_url})
        return result

### Vehicle Owner Log

class CarOwnersLogSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()
    primary_leasing_agent = serializers.SerializerMethodField()
 
    class Meta:
        model = Vehicle_Owner_Master
        fields = [
            "id",
            "business_name",
            "business_Email",
            "user_name",
            "phone_number",
            "created_at",
            "primary_leasing_agent",
        ]
 
    def get_user_name(self, obj):
        if obj.user_id:
            return f"{obj.user_id.first_name} {obj.user_id.last_name}"
        return None
 
    def get_primary_leasing_agent(self, obj):
        if obj.agency:
            return obj.agency.business_name
        return None

class CarOwnerDetailSerializer(serializers.ModelSerializer):
    primary_leasing_agency = serializers.SerializerMethodField()
    business_owner = serializers.SerializerMethodField()
    other_leasing_agencies = serializers.SerializerMethodField()
    class Meta:
        model = Vehicle_Owner_Master
        fields = [
            "id",
            "business_name",
            "business_Email",
            "address",
            "phone_number",
            "cac_number",
            "primary_leasing_agency",
            "business_owner",
            "other_leasing_agencies"
        ]
    def get_primary_leasing_agency(self, obj):
        if obj.agency:
            return obj.agency.business_name
        return "Not Set"
 
    def get_business_owner(self, obj):
        if obj.user_id:
            return f"{obj.user_id.first_name} {obj.user_id.last_name}"
        return None
 
    def get_other_leasing_agencies(self, obj):
        agencies = Lease_Agency_Master.objects.exclude(id=obj.agency_id)
        other_agency = [a.business_name for a in agencies]
        return None

## Vehicle Log
class VehicleOwnerListSerializer(serializers.Serializer):
    id = serializers.CharField()
    business_name = serializers.CharField()
    email = serializers.CharField()
    business_owner = serializers.CharField()
    phone = serializers.CharField()
    nos_of_vehicles = serializers.IntegerField()
    agency_affiliates = serializers.IntegerField()
    total_nos_leases = serializers.IntegerField()

class AgencyVehiclesSerializer(serializers.ModelSerializer):
    completed_leases = serializers.SerializerMethodField()
    status = serializers.CharField(source="vehicle_status.vehicle_status_name", default=None)
    report = serializers.SerializerMethodField()
    created_at = serializers.SerializerMethodField()
 
    class Meta:
        model = Vehicle_Master
        fields = [
            "id",
            "vehicle_make",
            "vehicle_model",
            "plate_number",
            "body_type",
            "mfg_year",
            "created_at",
            "completed_leases",
            "status",
            "report",
        ]
 
    def get_created_at(self, obj):
        return obj.created_at.strftime("%Y-%m-%d")
 
    def get_completed_leases(self, obj):
        return LeaseOrderMaster.objects.filter(
            vehicle=obj,
            order_status__order_status_name="completed"
        ).count()
 
    def get_report(self, obj):
        return None

class OwnerVehicleDetailSerializer(serializers.ModelSerializer):
    vehicle_brand = serializers.SerializerMethodField()
    vehicle_owner_telephone = serializers.CharField(source="vehicle_owner.phone_number")
    images = serializers.SerializerMethodField()
    agency = serializers.SerializerMethodField()

    class Meta:
        model = Vehicle_Master
        fields = [
            "id",
            "vehicle_brand",
            "plate_number",
            "vehicle_identify_number",
            "primary_location",
            "agency",
            "license_renewed_date",
            "license_expiry_date",
            "insurance_renewed_date",
            "insurance_expiry_date",
            "vehicle_owner_telephone",
            "active",
            "mfg_year",
            "images",
        ]

    def get_vehicle_brand(self, obj):
        return f"{obj.vehicle_make} {obj.vehicle_model}"

    def get_images(self, obj):
        images = Vehicle_Image.objects.filter(vehicle_master=obj)
        request = self.context.get("request")

        result = []
        for img in images:
            if img.image:
                url = request.build_absolute_uri(img.image.url) if request else f"{settings.MEDIA_URL}{img.image.name}"
                result.append({"image": url})
        return result

    def get_agency(self, obj):
        agencies = Vehicle_Agency.objects.filter(vehicle_master=obj, status="Active")

        result = []
        for agency in agencies:
            lease = agency.lease_agency
            result.append({
                "id": str(lease.id),
                "name": getattr(lease, "business_name", None)  # change to actual field
            })

        return result



## Agency Log

class AgencyLogListSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()
    vehicle_owner_count = serializers.SerializerMethodField()
 
    class Meta:
        model = Lease_Agency_Master
        fields = [
            "id",
            "business_name",
            "user_name",
            "business_Email",
            "phone_number",
            "created_at",
            "vehicle_owner_count"
        ]
 
    def get_user_name(self, obj):
        return f"{obj.user_id.first_name} {obj.user_id.last_name}"
 
    def get_vehicle_owner_count(self, obj):
        return Vehicle_Owner_Master.objects.filter(agency=obj).count()
 
class AgencyVehicleDetailSerializer(serializers.ModelSerializer):
    vehicle_type = serializers.SerializerMethodField()
    vehicle_owner = serializers.CharField(source="vehicle_owner.business_name")
    mfg_year = serializers.IntegerField()
    completed_lease = serializers.SerializerMethodField()
 
    class Meta:
        model = Vehicle_Master
        fields = [
            "id",
            "vehicle_type",
            "vehicle_owner",
            "body_type",
            "vehicle_identify_number",
            "mfg_year",
            "created_at",
            "primary_location",
            "completed_lease",
        ]
 
    def get_vehicle_type(self, obj):
        return f"{obj.vehicle_make} {obj.vehicle_model}"
 
    def get_completed_lease(self, obj):
        return LeaseOrderMaster.objects.filter(
            vehicle=obj,
            order_status__order_status_name="completed"
        ).count()

### Transaction Log

class TransactionLogSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="invoice.lease_order.order_number")
    client = serializers.SerializerMethodField()
    vehicle_type = serializers.SerializerMethodField()
    leasing_agent = serializers.CharField(source="invoice.lease_order.agency.business_name")
    lease_date = serializers.DateField(source="invoice.lease_order.start_date")
    lease_fee = serializers.IntegerField(source="invoice.total_amount")
    invoice_number = serializers.CharField(source="invoice.invoice_number")
    invoice_url = serializers.SerializerMethodField()
    class Meta:
        model = PaymentMaster
        fields = [
            "order_number",
            "client",
            "vehicle_type",
            "leasing_agent",
            "lease_date",
            "lease_fee",
            "payment_ref",
            "invoice_number",
            "paid_at",
            'invoice_url'
        ]
 
    def get_client(self, obj):
        user = obj.invoice.lease_order.user
        return f"{user.first_name} {user.last_name}"
    
    def get_vehicle_type(self, obj):
        return f"{obj.invoice.lease_order.vehicle.vehicle_make} {obj.invoice.lease_order.vehicle.vehicle_model},{obj.invoice.lease_order.vehicle.mfg_year}"
    
    def get_invoice_url(self, obj):
        request = self.context.get("request")
        return request.build_absolute_uri(obj.invoice.invoice_pdf.url) if obj.invoice.invoice_pdf else None
    
class PolicyMasterSerializer(serializers.ModelSerializer):
    class Meta:
        model = PolicyMaster
        fields = "__all__"

class SetCommissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = SetCommissionMaster
        fields = "__all__"

class VehiclePriceMatrixSerializer(serializers.ModelSerializer):
    class Meta:
        model = VehiclePriceMatrix
        fields = "__all__"
