"""
URL configuration for Admin_panel project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
# from django.contrib import admin
from django.urls import path,include
from .views import *
from django.conf.urls.static import static
from django.conf import settings
from Myadmin.AdminApp import views


urlpatterns = [
    
    path('api/v1/auth/Register', views.UserRegistrationAPI.as_view(), name='Register'),
    path('api/v1/auth/RegisterVerifyOtp', views.OtpVerificationAPI.as_view(), name='RegisterVerifyOtp'),
    path('api/v1/auth/ResendOtp', views.OtpResendAPI.as_view(), name='ResendOtp'),
        
    # login
    path('api/v1/auth/Login', views.LoginAPI.as_view(), name='Login'),
    path('api/v1/auth/LoginVerifyOtp', views.VerifyLoginAPI.as_view(), name='LoginVerifyOtp'),
    path('api/v1/auth/LoginResendOtp', views.LoginOtpResendAPI.as_view(), name='LoginResendOtp'),
    
    
    path('api/v1/auth/ForgotPassword', views.ForgotPasswordAPI.as_view(), name='forgotPassword'),
    path('api/v1/auth/VerifyPasswordOtp', views.Forgot_Otp_API.as_view(), name='VerifyPasswordOtp'),
    path('api/v1/auth/ResendPasswordOtp', views.Resend_Forgot_Otp_API.as_view(), name='ResendPasswordOtp'),
    path('api/v1/auth/ResetPassword', views.Reset_Password_API.as_view(), name='ResetPassword'),
    
    path('api/v1/auth/Logout', views.LogoutAPI.as_view(), name='LogoutAPI'),
  
] 



