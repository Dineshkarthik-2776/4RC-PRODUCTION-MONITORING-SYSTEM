from django.urls import path
from . import views

urlpatterns = [
    path('summary/', views.download_summary_report, name='summary_report'),
]