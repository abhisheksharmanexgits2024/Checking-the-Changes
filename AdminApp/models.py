from django.db import models
import uuid
from .manager import*
from datetime import datetime
from django.utils import timezone
from django.utils.timezone import now
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager, Group, Permission
from django.contrib.sessions.models import AbstractBaseSession
from User.models import *
# my models

# class TempUser(models.Model):
#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     fname = models.CharField(max_length=150, null=False)
#     phone_number = models.BigIntegerField(unique=False,null=False, blank=False) 
#     email = models.EmailField(unique=False, null=False)
#     password = models.CharField(max_length=255, null=False)
#     otp = models.CharField(max_length=6, null=True)  
#     otp_time_limit = models.DateTimeField(null=True, blank=True)  
#     created_at = models.DateTimeField(auto_now_add=True) 

#     USERNAME_FIELD = 'email'
#     REQUIRED_FIELDS = ['fname', 'phone_number']

#     # objects = RegisterUserManager()

#     def __str__(self):
#         return self.email
    
#     def is_otp_valid(self):
#         """Check if OTP is still valid (within 3 minutes)."""
#         if self.otp_time_limit:
#             return datetime.now() <= self.otp_time_limit
#         return False
    
#     class Meta:
#         db_table = 'temp_User'


# class Registration(AbstractBaseUser,PermissionsMixin):
#     ROLE_CHOICES = (
#         ('Platform_Admin','Platform_Admin'),
#         ('Super_Admin','Super_Admin'),
#         ('Admin','Admin'),
#         ('User','User')
#     )

#     STATUS_CHOICES = (
#         ('Active','Active'),
#         ('Deactive','Deactive')
#     )

#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     profile_photo = models.ImageField(upload_to="profile_photo/", null=True,blank=True)
#     fname = models.CharField(max_length=150, null=False)    
#     email = models.EmailField(unique=True)
#     password = models.CharField(max_length=255, null=False)
#     phone_number = models.BigIntegerField(unique=True,null=False, blank=False)
#     updated_at = models.DateTimeField(auto_now=True)
#     registration_date = models.DateTimeField(auto_now_add=True)
   
#     role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='User')
#     account_status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='Active')

#     is_active = models.BooleanField(default=True)
#     is_staff = models.BooleanField(default=False) 
#     is_superuser = models.BooleanField(default=False)

#     USERNAME_FIELD = 'email'
#     REQUIRED_FIELDS = ['fname','phone_number']

#     class Meta:
#         db_table = 'register_user'

#     objects = RegisterUserManager()


# class UserActivityLog(models.Model):
#     ACTION_CHOICES=[
#         ('Login', 'Login'),
#         ('Logout','Logout'),
#         ('Profile_Update','Profile_Update'),

#     ]

#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     user = models.ForeignKey(Registration, on_delete=models.CASCADE, null=True)
#     action = models.CharField(max_length=20, choices=ACTION_CHOICES)
#     timestamp = models.DateTimeField(auto_now_add=True)

#     class Meta:
#         db_table = 'customer_activity_log'

# class LoginOTP(models.Model):
#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     user = models.ForeignKey(Registration,on_delete=models.CASCADE)
#     otp = models.CharField(max_length=6, null=True)   
#     otp_time_limit = models.DateTimeField(null=True, blank=True) 
#     created_at_otp = models.DateTimeField(default=now) 

#     class Meta:
#         db_table = 'Login_otp'

# class ForgotPassword(models.Model):
#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     user = models.ForeignKey(Registration,on_delete=models.CASCADE)
#     otp = models.CharField(max_length=6, null=True) 
#     created_at = models.DateTimeField(default=now)

#     class Meta:
#         db_table = 'forgot_password_table'


# class CustomSession(AbstractBaseSession):
#     user = models.ForeignKey(Registration,on_delete=models.CASCADE,db_column="user_id",to_field="id")
#     ip_address = models.GenericIPAddressField(null=False)
#     expire_date = models.DateTimeField(null=False)  

#     class Meta:
#         db_table = 'custome_session'

#     def __str__(self):
#         return f"Session {self.session_key} - {self.user.email}"
    
        