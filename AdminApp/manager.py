# from django.db import models
# from django.contrib.auth.models import BaseUserManager


# class RegisterUserManager(models.Manager):
#     def create_customer(self, fname, phone_number, email, password):
#         customer = self.model(
#             fname=fname,
#             phone_number=phone_number,
#             email=email,
#             password=password
#         )
#         customer.save(using=self._db)
#         return customer

# class RegisterUserManager(BaseUserManager):
#     def create_user(self, email, fname, phone_number, password=None):
#         """Create and return a regular user."""
#         if not email:
#             raise ValueError("Users must have an email address")

#         user = self.model(
#             email=self.normalize_email(email),
#             fname=fname,
#             phone_number=phone_number,
#         )
#         user.set_password(password)
#         user.save(using=self._db)
#         return user

#     def create_superuser(self, email, fname, phone_number, password=None):
#         """Create and return a superuser with admin permissions."""
#         user = self.create_user(email, fname, phone_number, password)
#         user.is_staff = True  
#         user.is_superuser = True 
#         user.save(using=self._db)
#         return user
