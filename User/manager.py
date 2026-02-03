from django.contrib.auth.models import BaseUserManager



class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        from .models import User_Type  # avoid circular import

        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("account_status", "Active")

        # automatically assign Admin user type
        admin_type = User_Type.objects.filter(name__iexact="Admin").first()
        if admin_type:
            extra_fields.setdefault("user_type", admin_type)
        else:
            raise ValueError("User_Type with name 'Admin' must exist before creating a superuser.")

        return self.create_user(email, password, **extra_fields)
