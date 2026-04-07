from django.urls import path,re_path
from . import views

urlpatterns = [
    path('inspectors/', views.inspector_list, name='inspector_list'),
    path('inspectors/create/', views.inspector_create, name='inspector_create'),
    re_path(r'^inspectors/(?P<pk>[0-9a-fA-F]{24})/$', views.inspector_detail, name='inspector_detail'),
    re_path(r'^inspectors/(?P<pk>[0-9a-fA-F]{24})/update/$', views.inspector_update, name='inspector_update'),
    re_path(r'^inspectors/(?P<pk>[0-9a-fA-F]{24})/delete/$', views.inspector_delete, name='inspector_delete'),
    path('institutes/', views.institute_list, name='institute_list'),
    re_path(r'^institutes/(?P<pk>[0-9a-fA-F]{24})/$', views.institute_detail, name='institute_detail'),
    re_path(r'^institutes/(?P<pk>[0-9a-fA-F]{24})/update/$', views.institute_update, name='institute_update'),
    path('inspection-report/', views.inspection_report, name='inspection_report'),
    path('inspection-requests/', views.inspection_requests, name='inspection_requests'),
    path('inspector-reports/', views.inspector_reports, name='inspector_reports'),
    re_path(r'^schedule-inspection/(?P<request_id>[0-9a-fA-F]{24})/$', views.schedule_inspection, name='schedule_inspection'),
    re_path(r'^reject-inspection/(?P<request_id>[0-9a-fA-F]{24})/$', views.reject_inspection_request, name='reject_inspection_request'),
    re_path(r'^approve-inspection/(?P<request_id>[0-9a-fA-F]{24})/$', views.approve_inspection_request, name='approve_inspection_request'),
    re_path(r'^receive-inspection/(?P<request_id>[0-9a-fA-F]{24})/$', views.receive_inspection_schedule, name='receive_inspection_schedule'),
    re_path(r'^download-inspection-report/(?P<request_id>[0-9a-fA-F]{24})/$', views.download_inspection_report_admin, name='download_inspection_report_admin'),
    re_path(r'^complete-inspection/(?P<request_id>[0-9a-fA-F]{24})/$', views.complete_inspection, name='complete_inspection'),
]