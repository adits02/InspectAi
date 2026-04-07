
from django.urls import path
from . import views

urlpatterns = [

    path('login/', views.login_view, name='inspector_login'),
    path('view-reports/', views.view_reports, name='view_reports'),
    
    # path('discussion-forum/', views.discussion_forum, name='discussion_forum'),
    # path('discussion/<int:post_id>/', views.view_discussion, name='view_discussion'),
    # path('create-post/', views.create_post, name='create_post'),
    # path('create-reply/<int:post_id>/', views.create_reply, name='create_reply'),
    
    path('submit-feedback/', views.submit_feedback, name='submit_feedback'),
    path('feedback-page/', views.feedback_page, name='feedback_page'),
    path('view-inspection-requests/', views.view_inspection_requests_inspector, name='view_inspection_requests_inspector'),
    path('submit-inspection-report/<str:request_id>/', views.submit_inspection_report, name='submit_inspection_report'),
    path('download-inspection-report/<str:request_id>/', views.download_inspection_report, name='download_inspection_report'),
    path('view-uploaded-certificates/', views.view_certificates, name='view_certificates'),
    path('download-uploaded-certificate/<str:certificate_id>/', views.download_uploaded_certificate, name='download_uploaded_certificate'),
    path('download-supporting-document/<str:document_id>/', views.download_supporting_document, name='download_supporting_document'),
    path('verify-uploaded-certificate/<str:certificate_id>/', views.verify_certificate, name='verify_certificate'),
    path('view_images/<str:category>/', views.view_category_images, name='view_category_images'),
    path('api/images/<str:category>/', views.get_category_images_json, name='api_get_category_images'),
    path('download-deficiency-report/<str:report_id>/', views.download_deficiency_report, name='download_deficiency_report'),
  
]