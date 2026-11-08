from django.urls import path

from .views import (
    ChangeOwnPasswordView,
    ClaimAccountView,
    CurrentUserView,
    LoginView,
    LogoutView,
    UserDetailView,
    UserListCreateView,
    UserResetPasswordView,
    VerifyOwnPasswordView,
)

urlpatterns = [
    path('login/', LoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('claim/', ClaimAccountView.as_view(), name='claim-account'),
    path('me/', CurrentUserView.as_view(), name='current-user'),
    path('me/verify-password/', VerifyOwnPasswordView.as_view(),
         name='verify-own-password'),
    path('me/change-password/', ChangeOwnPasswordView.as_view(),
         name='change-own-password'),

    path('users/', UserListCreateView.as_view(), name='user-list-create'),
    path('users/<str:employee_id>/', UserDetailView.as_view(), name='user-detail'),
    path('users/<str:employee_id>/reset-password/',
         UserResetPasswordView.as_view(), name='user-reset-password'),
]
