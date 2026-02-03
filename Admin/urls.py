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
from django.contrib import admin
from django.urls import path,include
from AdminApp.views import *
from django.conf.urls.static import static
from django.conf import settings

from AdminApp import views

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
    
    path('api/v1/LeaseOrder',views.LeaseOrderAPI.as_view(), name='LeaseOrderList'),
    path('api/v1/LeaseOrder/<str:order_number>',views.LeaseOrderAPI.as_view(), name='LeaseOrderDetail'),
    
    path('api/v1/ScheduleLeaseOrder',views.ScheduleLeaseOrderAPI.as_view(), name='ScheduleLeaseOrderList'),
    path('api/v1/ScheduleLeaseOrder/<str:order_number>',views.ScheduleLeaseOrderAPI.as_view(), name='ScheduleLeaseOrderList'),
    
    path("api/v1/vehicles", views.VehicleInventoryAPI.as_view(), name="VehicleInventory"),
    path("api/v1/vehicles/<uuid:vehicle_id>", views.VehicleInventoryAPI.as_view(), name="VehicleInventory"),

    path("api/v1/CarOwner", views.CarOwnersAPI.as_view(), name="CarOwnersList"),
    path("api/v1/CarOwner/<uuid:owner_id>", views.CarOwnersAPI.as_view(), name="CarOwners Details"),

    path("api/v1/VehicleLogs", views.VehicleLogAPI.as_view(),name="VehicleLogs"),
    path("api/v1/Vehicle/Action/<uuid:id>", VehicleActionAPI.as_view(),name="VehicleAction"),

    path("api/v1/VehicleOwner/AgencyLogs", views.AgencyLogAPI.as_view(), name="AgencyLogsList"),
    path("api/v1/VehicleOwner/AgencyLogs/<uuid:agency_id>", views.AgencyLogAPI.as_view(), name="AgencyLogs Details"),   
    
    path("api/v1/TransactionLog", views.TransactionLogAPI.as_view(), name="TransactionLogs"),

    path("api/v1/Policy", views.PolicyListAPI.as_view(), name="Policy List"),
    path("api/v1/Policy/<uuid:pk>", views.PolicyDetailAPI.as_view(), name="Policy Detail"),  
    
    path("api/v1/Commission", views.CommissionListAPI.as_view(), name="commission-list"),
    path("api/v1/Commission/<uuid:pk>", views.CommissionDetailAPI.as_view(), name="commission-detail"),

    path("api/v1/vehicle_price_matrix", views.VehiclePriceMatrixListAPI.as_view(),name="vehicle-price-matrix-list"), 
    path("api/v1/vehicle_price_matrix/<uuid:pk>", views.VehiclePriceMatrixDetailAPI.as_view(),name="vehicle-price-matrix-detail"),

    path("api/v1/vehicle_update/<uuid:vehicle_id>",views.VehicleUpdateAPI.as_view(),name="vehicle-update"),

    path('api/v1/auth/Logout', views.LogoutAPI.as_view(), name='LogoutAPI'),
  
] 



