import random
from .models import *
from django.core.mail import send_mail
from Myadmin.settings import EMAIL_HOST_USER
import logging

logger = logging.getLogger(__name__)

def send_otp_via_email(email):
    try:
        user_obj = AdminTempUser.objects.get(email=email)  # Check if user exists before sending email
        
        # Generate a 4-digit OTP
        otp = random.randint(1000, 9999)  
        user_obj.otp = otp
        user_obj.save()

        subject = "Your Account Verification Email"
        message = f'Your OTP is {otp}'
        
        send_mail(subject, message, EMAIL_HOST_USER, [email], fail_silently=False)  

    except AdminTempUser.DoesNotExist:
        logger.warning(f"OTP request for non-existent email: {email}") 

    return {"message": "If this email is registered, an OTP has been sent."}
