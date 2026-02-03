from datetime import datetime
from django.db import models
from django.utils.timezone import now
from django.utils import timezone
from datetime import timedelta
import uuid
from .manager import UserManager
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager, Group, Permission
from django.db import models,transaction
from django.contrib.sessions.models import AbstractBaseSession
from django.conf import settings


class AdminTempUser(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    first_name = models.CharField(max_length=150, null=False)
    phone_number = models.BigIntegerField(unique=False,null=False, blank=False) 
    email = models.EmailField(unique=False, null=False)
    password = models.CharField(max_length=255, null=False)
    otp = models.CharField(max_length=6, null=True)  
    otp_time_limit = models.DateTimeField(null=True, blank=True)  
    created_at = models.DateTimeField(auto_now_add=True) 

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'phone_number']

    objects = UserManager()

    def __str__(self):
        return self.email
    
    def is_otp_valid(self):
        """Check if OTP is still valid (within 3 minutes)."""
        if self.otp_time_limit:
            return datetime.now() <= self.otp_time_limit
        return False
    
    class Meta:
        db_table = 'admin_temp_user'
        
        
class User_Type(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_type_name = models.CharField(max_length=100, null=False, blank=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'User_Type'
        

##      Base User    ##
class User_Master(AbstractBaseUser,PermissionsMixin):
    STATUS_CHOICES = (
         ('Active','Active'),
         ('Deactive','Deactive')
     )
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    photo = models.ImageField(upload_to='users/', null=True, blank=True)
    nin_number = models.CharField(max_length=100, unique=False)
    first_name = models.CharField(max_length=150, null=False)
    last_name = models.CharField(max_length=150, null=False)
    gender = models.CharField(max_length=10)
    middle_name = models.CharField(max_length=250, null=True, blank=True)
    date_of_birth = models.DateTimeField(null=True, blank=True)
    email = models.EmailField(unique=True, null=False)
    password = models.CharField(max_length=255, null=False)
    phone_number = models.BigIntegerField(unique=False,null=False, blank=False)
    employment_status = models.CharField(max_length=150, null=True)
    marital_status = models.CharField(max_length=150, null=True)
    
    account_status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='Active')    
    # user_type field
    user_type = models.ForeignKey(User_Type,on_delete=models.CASCADE)
    
    # JSON field 
    raw_api_response = models.JSONField(blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'phone_number']
    
    def __str__(self):
        return f"{self.first_name} {self.last_name} {self.user_type}"
    
    class Meta:
        db_table ='User_Master'
        
    objects = UserManager()
    
    
class ContactInfo(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("User_Master", on_delete=models.CASCADE, related_name="contact_infos")
    owner_name = models.CharField(max_length=255, null=True,blank=True)
    email = models.EmailField(unique=False, null=True, blank=True)
    phone_number = models.BigIntegerField(unique=False,null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "contact_info"
            
      
class AdminLoginOTP(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User_Master,on_delete=models.CASCADE)
    otp = models.CharField(max_length=6, null=True)   
    otp_time_limit = models.DateTimeField(null=True, blank=True) 
    created_at_otp = models.DateTimeField(default=now) 

    class Meta:
        db_table = 'Login_otp'

## Forgot Password ##
class User_OTP_Master(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User_Master,on_delete=models.CASCADE)
    otp = models.CharField(max_length=6, null=True) 
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = 'user_otp_master'


class CustomSession(AbstractBaseSession):
    user = models.ForeignKey(User_Master,on_delete=models.CASCADE,db_column="user_id",to_field="id")
    ip_address = models.GenericIPAddressField(null=False)
    expire_date = models.DateTimeField(null=False)  

    class Meta:
        db_table = 'custome_session'

    def __str__(self):
        return f"Session {self.session_key} - {self.user.email}"

class AdminActivityLog(models.Model):
    ACTION_CHOICES=[
        ('Login', 'Login'),
        ('Logout','Logout'),
        ('Profile_Update','Profile_Update'),

    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User_Master, on_delete=models.CASCADE, null=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'customer_activity_log'    
        
# Lease Agency Account  ##
class Lease_Agency_Master(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.ForeignKey(User_Master, on_delete=models.CASCADE)
    agency_profile = models.ImageField(upload_to='lease/', null=True, blank=True)
    cac_number = models.CharField(max_length=100, blank=True, null=True)
    full_name = models.CharField(max_length=150, null=True, blank=True)
    business_name = models.CharField(max_length=255, null=False)
    business_Email = models.EmailField(unique=False, null=False)
    business_number = models.BigIntegerField(unique=False,null=False, blank=False)
    business_type = models.CharField(max_length=100, blank=True, null=True)
    phone_number = models.BigIntegerField(unique=False,null=False, blank=False)
    year = models.PositiveIntegerField(blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    address = models.TextField(null=True)
    company_name = models.CharField(max_length=255)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table ='Lease_Agency_Master'

##  Vehicle Owner  ##
class Vehicle_Owner_Master(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.ForeignKey(User_Master, on_delete=models.CASCADE)
    cac_number = models.CharField(max_length=100, blank=True, null=True)
    full_name = models.CharField(max_length=150, null=True, blank=True)
    business_name = models.CharField(max_length=255, null=False)
    business_Email = models.EmailField(unique=False, null=False)
    business_number = models.BigIntegerField(unique=False,null=False, blank=False)
    business_type = models.CharField(max_length=100, blank=True, null=True)
    phone_number = models.BigIntegerField(unique=False,null=False, blank=False)
    year = models.PositiveIntegerField(blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    address = models.TextField(null=True)
    company_name = models.CharField(null=True,max_length=255)
    agency = models.ForeignKey(Lease_Agency_Master, on_delete=models.CASCADE,blank=True,null=True)
    name_of_bank = models.CharField(max_length=100, blank=True, null=True)
    account_name = models.CharField(max_length=100, blank=True, null=True)
    account_number = models.BigIntegerField(unique=False,null=True, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table ='Vehicle_Owner_Master'
 
class Vehicle_Owner_Driver(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vehicle_owner = models.ForeignKey(
        Vehicle_Owner_Master,
        on_delete=models.CASCADE,
        related_name="drivers"
    )
    name = models.CharField(max_length=150, null=False, blank=False)
    email = models.EmailField(unique=False, null=False)
    phone_number = models.BigIntegerField(unique=False, null=False, blank=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "Vehicle_Owner_Driver"

    def __str__(self):
        return self.name 


## Vehicle Owner Agency ##

class Vehicle_Owner_Agency(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vehicle_owner = models.ForeignKey(Vehicle_Owner_Master, on_delete=models.CASCADE)
    lease_agency = models.ForeignKey(Lease_Agency_Master, on_delete=models.CASCADE, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table ='Vehicle_Owner_Agency'
 
class VehicleStatusMaster(models.Model):
    vehicle_status_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vehicle_status_name = models.CharField(max_length=50)
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "vehicle_status_master"
        verbose_name = "Vehicle Status"
        verbose_name_plural = "Vehicle Status Master"

    def __str__(self):
        return self.vehicle_status_name



##   Vehicle model  ##
class Vehicle_Master(models.Model):

    # STATUS_CHOICES = [
    #     ("idle", "idle"),
    #     ("scheduled", "scheduled"),
    #     ("active", "active"),
    #     ("offline", "offline"),
    #     ("maintenance", "maintenance"),
    #     ("attention", "attention"),
    #     ("expired", "expired"),
    # ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vehicle_owner = models.ForeignKey(Vehicle_Owner_Master, on_delete=models.CASCADE, related_name="vehicles")
    registered_owner = models.CharField(max_length=100, unique=False, null=True, blank= True)
    # Vehicle details
    plate_number = models.CharField(max_length=100)
    vehicle_make = models.CharField(max_length=100)
    vehicle_model = models.CharField(max_length=100)
    body_type = models.CharField(max_length=100)
    mfg_year = models.PositiveIntegerField()
    vehicle_identify_number = models.CharField(max_length=100)  
    # Documents & expiry dates
    license_renewed_date = models.DateField(blank=True, null=True)
    license_expiry_date = models.DateField(blank=True, null=True)
    insurance_renewed_date = models.DateField(blank=True, null=True)
    insurance_expiry_date = models.DateField(blank=True, null=True)
    road_worthiness_cert_date = models.DateField(blank=True, null=True)
    road_worthiness_expiry_date = models.DateField(blank=True, null=True)
    engine_spec = models.TextField(blank=True, null=True)
    other_spec = models.TextField(blank=True, null=True)
    primary_location = models.CharField(max_length=255)
    lease_price_per_day = models.PositiveIntegerField(blank=True, null=True)
    active = models.BooleanField(default=True)
    vehicle_status = models.ForeignKey(VehicleStatusMaster,on_delete=models.SET_NULL,null=True,blank=True,related_name="vehicles")
    passenger_count = models.IntegerField(blank=True,null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now =True)

    class Meta:
        db_table ='Vehicle'

    def save(self, *args, **kwargs):
        if not self.vehicle_status:
            try:
                self.vehicle_status = VehicleStatusMaster.objects.get(vehicle_status_name="idle")
            except VehicleStatusMaster.DoesNotExist:
                self.vehicle_status = None
        super().save(*args, **kwargs)
# class Vehicle_Agency(models.Model):

#     STATUS_CHOICES={
#         ("Active","Active"),
#         ("Deactive","Deactive"),
#     }

#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     vehicle_master = models.ForeignKey(Vehicle_Master ,on_delete=models.CASCADE)
#     lease_agency = models.ForeignKey(Lease_Agency_Master, default=uuid.uuid4, on_delete=models.CASCADE)
#     status = models.CharField(max_length=50, choices=STATUS_CHOICES)
#     created_at = models.DateTimeField(auto_now_add=True)
#     updated_at = models.DateTimeField(auto_now_add=True)

#     class Meta:
#         db_table ='Vehicle_Agency' 
class Vehicle_Agency(models.Model):

    STATUS_CHOICES = {
        ("Active", "Active"),
        ("Deactive", "Deactive"),
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vehicle_master = models.ForeignKey(
        Vehicle_Master,
        on_delete=models.CASCADE,
        related_name="vehicle_agencies"
    )
    lease_agency = models.ForeignKey(Lease_Agency_Master, on_delete=models.CASCADE)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'User_vehicle_agency'

 
class Vehicle_Image(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vehicle_master = models.ForeignKey(Vehicle_Master ,on_delete=models.CASCADE,related_name="images")
    image =  models.ImageField(upload_to='vehicle_images/')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:

        db_table = "Vehicle_Image"
 
    def __str__(self):

        return f"Image for {self.vehicle_master .registered_owner}"



# ================================
# 1. ORDER STATUS MASTER
# ================================

class OrderStatusMaster(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order_status_name = models.CharField(max_length=50, unique=True)
    description = models.CharField(max_length=150, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "order_status_master"
        verbose_name = "Order Status"
        verbose_name_plural = "Order Status Master"

    def __str__(self):
        return self.order_status_name

# ================================
# 1. Cancellation Reason Master
# ================================

class CancellationReasonMaster(models.Model):
    cancellation_reason_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reason_name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "cancellation_reason_master"
        verbose_name = "Cancellation Reason"
        verbose_name_plural = "Cancellation Reason Master"

    def __str__(self):
        return self.reason_name

# ================================
# 2. LEASE ORDER MASTER
# ================================

class LeaseOrderMaster(models.Model):
    lease_order_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order_number = models.CharField(max_length=20, unique=True, blank=True, null=True)
    user = models.ForeignKey("User_Master", on_delete=models.CASCADE, related_name="lease_orders")
    vehicle = models.ForeignKey("Vehicle_Master", on_delete=models.CASCADE, related_name="lease_orders")
    agency = models.ForeignKey("Lease_Agency_Master", on_delete=models.CASCADE, related_name="lease_orders")
    order_status = models.ForeignKey(OrderStatusMaster, on_delete=models.CASCADE, related_name="lease_orders")
    cancellation_reason = models.ForeignKey(CancellationReasonMaster,on_delete=models.SET_NULL,null=True,blank=True,db_column="cancellation_reason_id")
    purpose = models.CharField(max_length=150)
    state = models.CharField(max_length=100, blank=True, null=True)
    lease_type = models.CharField(max_length=50, choices=[("chauffeur", "chauffeur"), ("self_drive", "self_drive")], default="self_drive")
    leased_for = models.CharField(max_length=150)
    start_date = models.DateField()
    end_date = models.DateField()
    total_days = models.IntegerField()
    client_location = models.CharField(max_length=255)
    delivery_address = models.TextField()
    delivery_distance_km = models.DecimalField(max_digits=6, decimal_places=2,null=True)
    estimated_delivery_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    driver = models.ForeignKey(Vehicle_Owner_Driver, on_delete=models.SET_NULL, null=True,blank=True,related_name="chauffeur_orders")
    total_amount = models.IntegerField(blank=True, null=True)
    remaining_time = models.DateTimeField(blank=True, null=True)
    no_of_passenger = models.IntegerField(blank=True,null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    

    class Meta:
        db_table = "lease_order_master"
        verbose_name = "Lease Order"
        verbose_name_plural = "Lease Order Master"

    def __str__(self):
        return f"LeaseOrder {self.lease_order_id}"
    
    def save(self, *args, **kwargs):

        if not self.order_number:
            with transaction.atomic():
                last_order = (
                    LeaseOrderMaster.objects.select_for_update()
                    .order_by("created_at")
                    .last()
                )
                if last_order and last_order.order_number:
                    try:
                        # Extract number part, e.g., from "#000023" -> 23
                        last_number = int(last_order.order_number.replace("#", ""))
                    except ValueError:
                        last_number = 0
                else:
                    last_number = 0

                new_number = last_number + 1
                self.order_number = f"#{new_number:06d}"  #Generates "#000001", "#000002", etc.

        if not self.remaining_time:
            base_time = self.created_at if self.created_at else timezone.now()
            self.remaining_time = base_time + timedelta(minutes=settings.ORDER_PROCESSING_TIME)

        super().save(*args, **kwargs)


# ================================
# 3. INVOICE STATUS MASTER
# ================================
class InvoiceStatusMaster(models.Model):
    invoice_status_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice_status_name = models.CharField(max_length=50)
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "invoice_status_master"
        verbose_name = "Invoice Status"
        verbose_name_plural = "Invoice Status Master"

    def __str__(self):
        return self.invoice_status_name


# ================================
# 4. LEASE INVOICE
# ================================

class LeaseInvoice(models.Model):
    invoice_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice_number = models.CharField(max_length=20, unique=True, blank=True, null=True)
    lease_order = models.ForeignKey(LeaseOrderMaster, on_delete=models.CASCADE, related_name="invoices")
    invoice_status = models.ForeignKey(InvoiceStatusMaster, on_delete=models.CASCADE, related_name="invoices")
    micro_insurance = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    delivery_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    vat = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    note = models.TextField(null=True, blank=True)
    invoice_pdf = models.FileField(upload_to="invoices/", null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "lease_invoice"
        verbose_name = "Lease Invoice"
        verbose_name_plural = "Lease Invoices"

    def __str__(self):
        return f"Invoice {self.invoice_id}"
    
    def generate_agency_code(self):
        name = self.lease_order.agency.business_name.strip()

        words = name.split()
        if len(words) >= 3:
            return "".join(w[0].upper() for w in words[:3])
        elif len(words) == 2:
            return (words[0][0] + words[1][:2]).upper()
        else:
            return name[:3].upper()
        
    def get_lease_type_code(self):
        lt = self.lease_order.lease_type
        return "CHD" if lt == "chauffeur" else "SLF"

    def generate_invoice_number(self):
        agency_code = self.generate_agency_code()
        lease_code = self.get_lease_type_code()

        # Example: GIG-CHD-0001
        pattern_prefix = f"{agency_code}-{lease_code}-"

        with transaction.atomic():
            last_invoice = (
                LeaseInvoice.objects.select_for_update()
                .filter(invoice_number__startswith=pattern_prefix)
                .order_by("-invoice_number")
                .first()
            )

            if last_invoice:
                try:
                    last_serial = int(last_invoice.invoice_number.split("-")[-1])
                except:
                    last_serial = 0
                new_serial = last_serial + 1
            else:
                new_serial = 1

            return f"{pattern_prefix}{new_serial:04d}"

    # ---------------------------------------------------------
    # Override save()
    # ---------------------------------------------------------
    def save(self, *args, **kwargs):
        if not self.invoice_number:
            self.invoice_number = self.generate_invoice_number()

        super().save(*args, **kwargs)

    # def save(self, *args, **kwargs):
    #     if not self.invoice_number:
    #         prefix = "SC01"

    #         with transaction.atomic():
    #             # Lock table rows to prevent concurrent reads of the same "last invoice"
    #             last_invoice = (
    #                 LeaseInvoice.objects.select_for_update()
    #                 .filter(invoice_number__startswith=prefix)
    #                 .order_by("invoice_number")
    #                 .last()
    #             )
    #             if last_invoice:
    #                 try:
    #                     last_number = int(last_invoice.invoice_number.split("/")[-1])
    #                 except (IndexError, ValueError):
    #                     last_number = 0
    #                 new_number = last_number + 1
    #             else:
    #                 new_number = 1
    #             self.invoice_number = f"{prefix}/{new_number:03d}"
    #             super().save(*args, **kwargs)
    #     else:
    #         super().save(*args, **kwargs)


# ================================
# 5. PAYMENT STATUS MASTER
# ================================
class PaymentStatusMaster(models.Model):
    payment_status_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment_status_name = models.CharField(max_length=50)
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payment_status_master"
        verbose_name = "Payment Status"
        verbose_name_plural = "Payment Status Master"

    def __str__(self):
        return self.payment_status_name


# ================================
# 6. PAYMENT METHOD MASTER
# ================================
class PaymentMethodMaster(models.Model):
    payment_method_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment_method_name = models.CharField(max_length=50)
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payment_method_master"
        verbose_name = "Payment Method"
        verbose_name_plural = "Payment Method Master"

    def __str__(self):
        return self.payment_method_name


# ================================
# 7. PAYMENT MASTER
# ================================
class PaymentMaster(models.Model):
    payment_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(LeaseInvoice, on_delete=models.CASCADE, related_name="payments")
    transaction_id = models.CharField(max_length=150, null=True, blank=True)
    payment_method = models.ForeignKey(PaymentMethodMaster, on_delete=models.CASCADE, related_name="payments")
    payment_status = models.ForeignKey(PaymentStatusMaster, on_delete=models.CASCADE, related_name="payments")
    payment_ref = models.CharField(max_length=150, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    # currency = models.CharField(max_length=10, default="NGN")
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payment_master"
        verbose_name = "Payment"
        verbose_name_plural = "Payment Master"

    def __str__(self):
        return f"{self.payment_ref} ({self.payment_status.payment_status_name})"


# ================================
# 8. LEASE REVIEW MASTER
# ================================
class LeaseReviewMaster(models.Model):
    review_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lease_order = models.ForeignKey(LeaseOrderMaster, on_delete=models.CASCADE, related_name="reviews")
    user = models.ForeignKey("User_Master", on_delete=models.CASCADE, related_name="lease_reviews")
    rating = models.IntegerField()
    comment = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "lease_review_master"
        verbose_name = "Lease Review"
        verbose_name_plural = "Lease Review Master"

    def __str__(self):
        return f"Review {self.rating}★ by {self.user_id}"

class PolicyMaster(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    policy_name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True, null=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "Policy_Master"

    def __str__(self):
        return self.policy_name   
    
class SetCommissionMaster(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    commission_name = models.CharField(max_length=150, unique=True)
    value = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "Set_Commission_Master"

    def __str__(self):
        return self.commission_name    
    

class VehiclePriceMatrix(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vehicle_make = models.CharField(max_length=128)
    vehicle_model = models.CharField(max_length=128)
    vehicle_class = models.CharField(max_length=64, null=True, blank=True)
    vehicle_location = models.CharField(max_length=128, null=True, blank=True)
    vehicle_year = models.PositiveSmallIntegerField(null=True, blank=True)

    lease_per_day = models.IntegerField()
    delivery_rate_per_km = models.IntegerField(null=True, blank=True)
    micro_insurance_rate_per_rider = models.IntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "vehicle_price_matrix"
        verbose_name = "Vehicle Price Matrix"
        verbose_name_plural = "Vehicle Price Matrix Records"

    def __str__(self):
        return f"{self.vehicle_make} {self.vehicle_model} ({self.vehicle_year})"



##   Driver  ##
class Driver(models.Model):
    pass