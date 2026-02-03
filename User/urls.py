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
from User import views
from django.conf.urls.static import static
from django.conf import settings

urlpatterns = [
    path('api/v1/GetNin', views.VerifyNINAPI.as_view(), name='GetNin'),
    path('api/v1/GetCac', views.VerifyCACAPI.as_view(), name='GetCac'),
    
    path('api/v1/Register', views.RegisterAPI.as_view(), name='Register'),  
    path('api/v1/Login', views.LoginAPI.as_view(), name='Login'),
    path('api/v1/ForgotPassword', views.Forgot_passwordAPI.as_view(), name='ForgotPassword'),
    path('api/v1/VerifyOtp', views.Forgot_Otp_API.as_view(), name='VerifyOtp'),
    path('api/v1/ResetVerifyOtp', views.Resend_Forgot_Otp_API.as_view(), name='ResetVerifyOtp'),
    path('api/v1/ResetPassword', views.Reset_Password_API.as_view(), name='ResetPassword'),

    path('api/v1/UserType', views.UserTypeCreateAPI.as_view(), name='UserType'),
    
    path('api/v1/GetUser', views.GetUserProfileAPI.as_view(), name='GetUser'),

    path('api/v1/GetLeaseAgency', views.GetLeaseAgencyAPI.as_view(), name='GetLeaseAgency'),
    
    # path('api/v1/GetUserbusiness', views.GetUserBusinessAPI.as_view(), name='GetUserbusiness'),
    # path('api/v1/UpdateUserAPI', views.UpdateUserAPI.as_view(), name='UpdateUserAPI'),
    
    path('api/v1/CreateVehicle', views.VehicleCreateAPI.as_view(), name='CreateVehicle'),
    
    path('api/v1/Vehicles', views.GetVehicleListAPI.as_view(), name='Vehicles'),
    path('api/v1/Vehicles/<uuid:id>', views.GetVehicleListAPI.as_view(), name='Vehicles'),
    
    
    path('api/v1/LeaseVehicles', views.GetLeaseVehicleAPI.as_view(), name='LeaseVehicles'),
    path('api/v1/LeaseVehicles/<uuid:id>', views.GetLeaseVehicleAPI.as_view(), name='LeaseVehicles'),


    path('api/v1/LeaseVehiclesOpen', views.GetLeaseVehicleOpenAPI.as_view(), name='GetLeaseVehicleOpenAPI'),
    path('api/v1/LeaseVehiclesOpen/<uuid:id>', views.GetLeaseVehicleOpenAPI.as_view(), name='GetLeaseVehicleOpenAPI'),

    path('api/v1/CreateOrder', views.CreateBookingLeaseOrderAPI.as_view(), name='CreateOrder'),
   
    path('api/v1/OrderDetails', views.GetOrderDetailAPI.as_view(), name='OrderDetails'),
    path('api/v1/OrderDetails/<uuid:lease_order_id>', views.GetOrderDetailAPI.as_view(), name='OrderDetails'),   
    
    path('api/v1/InReviewOrder', views.AgencyUpdateLeaseOrderAPI.as_view(), name='InReviewOrder'),

    path('api/v1/confirmOrder', views.OwnerConfirmLeaseOrderAPI.as_view(), name='confirmOrder'),
    
    path('api/v1/LeaseOrder/Details/', views.GetStatusDetailsAPI.as_view(), name='get_status_details'),
    path('api/v1/LeaseOrder/Details/<uuid:lease_order_id>/', views. GetStatusDetailsAPI.as_view(), name='get_status_details_by_id'),
    
    path('api/v1/verifyAPI', views.VerifyAPI.as_view(), name='verify-api'),
    
    path('api/v1/VerifyPlate', views.VerifyPlateNumber.as_view(), name='verify_plate'),
    
    path('api/v1/Dashboard', views.DashboardAPIView.as_view(), name='Dashboard'),

    path('api/v1/LogOut', views.LogOut.as_view(), name='LogOut'),
    
    path('api/v1/GetAgencyOrder', views.GetAgencyOrder.as_view(), name='GetAgencyOrder'),
   
    path('api/v1/GetOrdersByStatusAPI', views.GetOrdersByStatusAPI.as_view(), name='GetOrdersByStatusAPI'),
   
    path('api/v1/GetAgencyBookedOrder', views.GetAgencyBookedOrder.as_view(), name='GetAgencyBookedOrder'),

    path('api/v1/CreateInvoiceAPI', views.CreateInvoiceAPI.as_view(), name='CreateInvoiceAPI'),

    path('api/v1/ListInvoicesAPI', views.ListInvoicesAPI.as_view(), name='ListInvoicesAPI'),

    path('api/v1/SendInvoiceAPI', views.SendInvoiceAPI.as_view(), name='SendInvoiceAPI'),

    path('api/v1/schedule_Order', views.OwnerScheduleLeaseOrderAPI.as_view(), name='schedule_Order'),

    path ('api/v1/Agency_Schedule_Order', views.AgencyScheduleLeaseOrderAPI.as_view(), name="AgencyScheduleLeaseOrderAPI"),

    path('api/v1/GetInvoiceAPI', views.GetInvoiceAPI.as_view(), name='GetInvoiceAPI'),

    path('api/v1/CreatePayment', views.CreatePaymentAPI.as_view(), name='CreatePaymentAPI'),

    path('api/v1/UpdateInvoice', views.UpdateInvoiceAPI.as_view(), name='UpdateInvoiceAPI'),

    path('api/v1/CancelOrder', views.CancelOrderAPI.as_view(), name='CancelOrderAPI'),

    path('api/v1/PublicVehicleNameList', views.PublicVehicleNameListAPI.as_view(), name='PublicVehicleNameListAPI'),

    path('api/v1/PublicVehicleSearch', views.PublicVehicleSearchAPI.as_view(), name='PublicVehicleSearchAPI'),

    path('api/v1/RiderOrderList', views.RiderOrderListAPI.as_view(), name='RiderOrderListAPI'),

    path('api/v1/Vehicles/deactivate', views.DeactivateVehicleAPI.as_view(), name='DeactivateVehicle'),

    path('api/v1/GetOwnerDrivers', views.GetOwnerDriversAPI.as_view(), name='GetOwnerDriversAPI'),

    path('api/v1/UpdateRider', views.UpdateRiderAPI.as_view(), name='UpdateRiderAPI'),

    path('api/v1/UpdateLeaseAgency', views.UpdateLeaseAgencyAPI.as_view(), name='UpdateLeaseAgencyAPI'),

    path('api/v1/UpdateVehicleOwner', views.UpdateVehicleOwnerAPI.as_view(),name='UpdateVehicleOwnerAPI'),

    path('api/v1/GetLegalContentData', views.PolicyDetailAPI.as_view(),name='PolicyDetailAPI'),

    path('api/v1/DeleteRider',views.DeleteRiderAPI.as_view(),name="DeleteRider"),

    path('api/v1/DeleteAgency',views.DeleteLeaseAgencyAPI.as_view(),name="DeleteAgency"),

    path('api/v1/DeleteVehicleOwner',views.DeleteVehicleOwnerAPI.as_view(),name="DeleteVehicleOwner"),

    path('api/v1/ImageConverter', views.ImageConverter.as_view(), name='ImageConverter'),  
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)