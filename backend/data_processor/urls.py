from django.urls import path
from .views import *

urlpatterns = [
    path("recipe-master/", RecipeMasterAPIView.as_view() , name='recipe-master-list'),
    path('recipe-master/<str:pk>/', RecipeMasterUpdateAPIView.as_view(), name='recipe-master-update'),
    path('recipe-upload/', RecipeUploadAPIView.as_view(), name='recipe-upload'),
    path('recipe-export-master/', RecipeExportAPIView.as_view(), name='recipe-export'),
    path('changeover/update/<int:pk>/',ChangeoverUpdateAPIView.as_view(), name='changeover-update-api'),
    path('changeover-stats/', ChangeoverStatsAPIView.as_view(), name='changeover-stats-api'),
    path('changeover-export/', ChangeoverExportAPIView.as_view(), name='changeover-export'),
    path('kepware-logs-export/', KepwareLogsExportAPIView.as_view(), name='kepware-logs-export'),
    path('missing-recipes-warning/', MissingRecipeWarningAPIView.as_view(), name='missing-recipes-warning'),
    path('correction-requests/', CorrectionRequestListAPIView.as_view(), name='correction-request-list'),
    path('correction-requests/<int:pk>/action/', CorrectionRequestActionAPIView.as_view(), name='correction-request-action'),
    path('standard-time/', StandardTimeListCreateAPIView.as_view(), name='standard-time-list-create'),
    path('standard-time/<int:pk>/', StandardTimeDeleteAPIView.as_view(), name='standard-time-delete'),
    path('standard-time/<int:pk>/rules/', StandardTimeRuleAPIView.as_view(), name='standard-time-rule-list-create'),
    path('standard-time/<int:pk>/rules/<int:rule_id>/', StandardTimeRuleDeleteAPIView.as_view(), name='standard-time-rule-delete'),
    path('overshoot-options/', OvershootOptionsAPIView.as_view(), name='overshoot-options'),

    # --- Operator-wise setup capture ---
    # Stoppages are recorded live and matched to a changeover later, so
    # they are their own resource rather than nested under one.
    path('line-stoppages/', LineStoppageListCreateAPIView.as_view(),
         name='line-stoppage-list-create'),
    path('line-stoppages/current/', CurrentStoppageAPIView.as_view(),
         name='line-stoppage-current'),
    path('line-stoppages/<int:pk>/', LineStoppageDetailAPIView.as_view(),
         name='line-stoppage-detail'),
    path('line-stoppages/<int:pk>/review/', StoppageReviewAPIView.as_view(),
         name='line-stoppage-review'),
    path('line-stoppages/<int:pk>/handover/', StoppageHandoverAPIView.as_view(),
         name='line-stoppage-handover'),
    path('setup-time/summary/', SetupTimeSummaryAPIView.as_view(),
         name='setup-time-summary'),
    path('setup-time/<int:pk>/', ChangeoverSetupDetailAPIView.as_view(),
         name='changeover-setup-detail'),
    path('setup-time/<int:pk>/attribute/', SetupAttributionAPIView.as_view(),
         name='setup-attribution'),
    path('duty/operators/', AssignableOperatorsAPIView.as_view(),
         name='assignable-operators'),
    path('duty/status/', DutyStatusAPIView.as_view(), name='duty-status'),
    path('duty/sessions/', DutySessionListAPIView.as_view(), name='duty-session-list'),
]
