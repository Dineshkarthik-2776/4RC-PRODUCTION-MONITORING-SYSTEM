from django.db import models
from django.contrib.auth.models import AbstractBaseUser , PermissionsMixin  , BaseUserManager
# Create your models here.
class UserManager(BaseUserManager):
    def create_user(self,email,password,role = 'worker',**extra_fields):
        if not email:
            raise ValueError('Email is required')
        email = self.normalize_email(email)
        user = self.model(email=email,role = role , **extra_fields)
        user.set_password(password)
        user.save()
        return user

    def create_superuser(self,email,password,**extra_fields):
        extra_fields.setdefault('is_staff' , True)
        extra_fields.setdefault('is_superuser' , True)
        user = self.create_user(email, password,'admin', **extra_fields)
        return user


class User(AbstractBaseUser,PermissionsMixin):
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('manager', 'Manager'),
        ('worker', 'Worker'),
    ]

    email = models.EmailField(max_length=255,unique=True)
    role  = models.CharField(max_length=10, choices=ROLE_CHOICES,default='worker')
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = UserManager()
    USERNAME_FIELD = 'email'

    def __str__(self):
        return f"{self.email} ({self.role})"
