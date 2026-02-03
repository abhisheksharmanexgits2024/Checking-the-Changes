from django.contrib import admin
from .models import*


# ================================
# Admin for User_Type
# ================================
class UserTypeAdmin(admin.ModelAdmin):
    list_display = ("id", "user_type_name")
    search_fields = ("user_type_name",)

admin.site.register(User_Type, UserTypeAdmin)

# ================================
# Admin for User_Master
# ================================
class UserMasterAdmin(admin.ModelAdmin):
    list_display = (
        "id", "first_name", "last_name", "email", "phone_number", "user_type", "employment_status",
        "marital_status", "created_at", "updated_at"
    )
    search_fields = ("first_name", "last_name", "email", "phone_number")
    list_filter = ("user_type", "employment_status", "marital_status")
    ordering = ("first_name",)

admin.site.register(User_Master, UserMasterAdmin)

# ================================
# Admin for User_OTP_Master
# ================================
class UserOTPAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "otp", "created_at")
    search_fields = ("user__first_name", "user__last_name", "otp")

admin.site.register(User_OTP_Master, UserOTPAdmin)

# ================================
# Admin for Lease_Agency_Master
# ================================
class LeaseAgencyAdmin(admin.ModelAdmin):
    list_display = (
        "id", "business_name", "business_Email", "business_number", "user_id", "company_name",
        "state", "year"
    )
    search_fields = ("business_name", "business_Email", "company_name")
    list_filter = ("state", "year", "business_type")
    ordering = ("business_name",)

admin.site.register(Lease_Agency_Master, LeaseAgencyAdmin)

# ================================
# Admin for Vehicle_Owner_Master
# ================================
class VehicleOwnerAdmin(admin.ModelAdmin):
    list_display = (
        "id", "business_name", "business_Email", "business_number", "user_id", "state", "year"
    )
    search_fields = ("business_name", "business_Email", "full_name")
    list_filter = ("state", "year", "business_type")

admin.site.register(Vehicle_Owner_Master, VehicleOwnerAdmin)

# ================================
# Admin for Vehicle_Owner_Agency
# ================================
class VehicleOwnerAgencyAdmin(admin.ModelAdmin):
    list_display = ("id", "vehicle_owner", "lease_agency")
    search_fields = ("vehicle_owner__business_name", "lease_agency__business_name")

admin.site.register(Vehicle_Owner_Agency, VehicleOwnerAgencyAdmin)

# ================================
# Admin for Vehicle_Master
# ================================
class VehicleMasterAdmin(admin.ModelAdmin):
    list_display = (
        "id", "vehicle_owner", "registered_owner", "plate_number", "vehicle_make", "vehicle_model", 
        "body_type", "mfg_year", "vehicle_identify_number", "primary_location", "lease_price_per_day",
        "vehicle_status", "created_at", "updated_at"
    )
    search_fields = ("vehicle_make", "vehicle_model", "plate_number", "vehicle_identify_number")
    list_filter = ("vehicle_status", "vehicle_make", "vehicle_model", "body_type")
    ordering = ("vehicle_make",)

admin.site.register(Vehicle_Master, VehicleMasterAdmin)

# ================================
# Admin for Vehicle_Agency
# ================================
class VehicleAgencyAdmin(admin.ModelAdmin):
    list_display = ("id", "vehicle_master", "lease_agency", "status", "created_at", "updated_at")
    search_fields = ("vehicle_master__plate_number", "lease_agency__business_name")
    list_filter = ("status",)

admin.site.register(Vehicle_Agency, VehicleAgencyAdmin)

# ================================
# Admin for Vehicle_Image
# ================================
class VehicleImageAdmin(admin.ModelAdmin):
    list_display = ("id", "vehicle_master","image","created_at", "updated_at")

admin.site.register(Vehicle_Image, VehicleImageAdmin)

# ================================
# Admin for OrderStatusMaster
# ================================
class OrderStatusMasterAdmin(admin.ModelAdmin):
    list_display = ("id", "order_status_name", "description", "created_at")
    search_fields = ("order_status_name", "description")

admin.site.register(OrderStatusMaster, OrderStatusMasterAdmin)

# ================================
# Admin for LeaseOrderMaster
# ================================
class LeaseOrderMasterAdmin(admin.ModelAdmin):
    list_display = (
        "lease_order_id", "user", "vehicle", "agency", "order_status", "purpose", "start_date", "end_date", 
        "total_days", "client_location", "created_at", "updated_at"
    )
    search_fields = ("user__first_name", "vehicle__plate_number", "agency__business_name", "lease_order_id")
    list_filter = ("order_status", "lease_type", "start_date", "end_date")
    ordering = ("start_date",)

admin.site.register(LeaseOrderMaster, LeaseOrderMasterAdmin)

# ================================
# Admin for InvoiceStatusMaster
# ================================
class InvoiceStatusMasterAdmin(admin.ModelAdmin):
    list_display = ("invoice_status_id", "invoice_status_name", "description", "is_active", "created_at")
    search_fields = ("invoice_status_name", "description")

admin.site.register(InvoiceStatusMaster, InvoiceStatusMasterAdmin)

# ================================
# Admin for LeaseInvoice
# ================================
class LeaseInvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "invoice_id","invoice_number", "lease_order", "invoice_status", "micro_insurance","subtotal", 
        "delivery_cost", "discount", "vat", "total_amount", "note", "due_date", "created_at"
    )
    search_fields = ("lease_order__lease_order_id", "invoice_status__invoice_status_name")
    list_filter = ("invoice_status",)

admin.site.register(LeaseInvoice, LeaseInvoiceAdmin)

# ================================
# Admin for PaymentStatusMaster
# ================================
class PaymentStatusMasterAdmin(admin.ModelAdmin):
    list_display = ("payment_status_id", "payment_status_name", "description", "is_active", "created_at")
    search_fields = ("payment_status_name", "description")

admin.site.register(PaymentStatusMaster, PaymentStatusMasterAdmin)

# ================================
# Admin for PaymentMethodMaster
# ================================
class PaymentMethodMasterAdmin(admin.ModelAdmin):
    list_display = ("payment_method_id", "payment_method_name", "description", "is_active", "created_at")
    search_fields = ("payment_method_name", "description")

admin.site.register(PaymentMethodMaster, PaymentMethodMasterAdmin)

# ================================
# Admin for PaymentMaster
# ================================
class PaymentMasterAdmin(admin.ModelAdmin):
    list_display = (
        "payment_id", "invoice", "transaction_id","payment_method", "payment_status", "payment_ref", "amount", 
        "paid_at", "created_at"
    )
    search_fields = ("payment_ref", "invoice__lease_order__lease_order_id", "payment_status__payment_status_name")
    list_filter = ("payment_status", "payment_method")

admin.site.register(PaymentMaster, PaymentMasterAdmin)

# ================================
# Admin for LeaseReviewMaster
# ================================
class LeaseReviewMasterAdmin(admin.ModelAdmin):
    list_display = ("review_id", "lease_order", "user", "rating", "comment", "created_at")
    search_fields = ("lease_order__lease_order_id", "user__first_name", "user__last_name")
    list_filter = ("rating",)

admin.site.register(LeaseReviewMaster, LeaseReviewMasterAdmin)