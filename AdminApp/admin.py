from django.contrib import admin
from .models import *

# myadmin/admin.py

# admin.site.site_header = "My Project Admin"
# admin.site.site_title = "Admin Portal"
# admin.site.index_title = "Dashboard"

# @admin.register(TempUser)
# class TempUserAdmin(admin.ModelAdmin):
#     list_display = (
#         'id','fname', 'email', 'phone_number', 'created_at','otp','otp_time_limit'
#     )
#     search_fields = ('email', 'fname')
#     ordering = ('-created_at',)

# @admin.register(Registration)
# class RegistrationAdmin(admin.ModelAdmin):
#     list_display = (
#        'id','profile_photo', 'fname', 'email','password', 'phone_number','account_status','role','updated_at','registration_date')
#     search_fields = ('email', 'fname')
#     # list_filter = ('account_status')
#     ordering = ('-registration_date',)


# class UserActivityAdmin(admin.ModelAdmin):
#     list_display=('id','action','timestamp','user_id')
# admin.site.register(UserActivityLog,UserActivityAdmin)


# class ForgotPasswordAdmin(admin.ModelAdmin):
#     list_display=('id','user_id','otp','created_at')
# admin.site.register(ForgotPassword,ForgotPasswordAdmin)


# class LoginAdmin(admin.ModelAdmin):
#     list_display = ('id','user','otp','otp_time_limit','created_at_otp')
# admin.site.register(LoginOTP,LoginAdmin)
