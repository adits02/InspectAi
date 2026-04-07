from django.db import models
from mongoengine import Document, StringField, EmailField, FileField,URLField,ListField,DictField, DateTimeField
from django import forms
from django.utils.timezone import now
from datetime import datetime

class College(Document):
    college_name = StringField(required=True)
    college_id = StringField(required=True, unique=True)
    pin_id = StringField(required=True)
    email = EmailField(required=True, unique=True)
    state = StringField(required=True)
    city = StringField(required=True)
    password = StringField(required=True)
    approved = StringField()

    meta = {
        'collection': 'college'  # Maps to the "college" collection in MongoDB
    }

class CollegeForm(forms.Form):
    college_name = forms.CharField(max_length=100, required=True, label="College Name")
    college_id = forms.CharField(max_length=100, required=True, label="College ID")
    pin_id = forms.CharField(max_length=10, required=True, label="PIN ID")
    email = forms.EmailField(required=True, label="Email")
    state = forms.CharField(max_length=100, required=True, label="State")
    city = forms.CharField(max_length=100, required=True, label="City")
    approved = forms.ChoiceField(choices=[('Pending', 'Pending'), ('Approved', 'Approved'), ('Rejected', 'Rejected')], required=True, label="Approval Status")

class certificate(Document):
    name = StringField(required=True) 
    file = FileField(required=True)
    college_name = StringField(required=True)
    field_name = StringField(required=True) 
    # Verification fields
    verified = StringField(default='Pending')  # 'Pending', 'Verified', 'Rejected'
    verified_by = StringField()
    verified_at = StringField()
    score = StringField()
    notes = StringField()
    format_details = DictField()

    meta = {
        'collection': 'certificate_unverified'
    }
    
    def url(self):
        # Ensure the URL is returned from the correct file field
        return self.file.url if self.file else None

class mandatory_dis(Document):
    name = StringField(required=True) 
    file = FileField(required=True)
    college_name = StringField(required=True)
    college_intake = StringField(required=True)

    meta = {
        'collection': 'mandatory_disclosure'
    }

class supporting_document(Document):
    name = StringField(required=True)
    file = FileField(required=True)
    college_name = StringField(required=True)
    field_name = StringField(required=True)
    uploaded_at = DateTimeField(default=datetime.utcnow)
    verified = StringField(default='Pending')
    verified_by = StringField()
    verified_at = StringField()
    score = StringField()
    notes = StringField()
    format_details = DictField()

    meta = {
        'collection': 'supporting_documents'
    }

class InspectionRequest(Document):
    college_name = StringField(required=True)
    request_reason = StringField(required=True)
    requested_date = DateTimeField(default=datetime.utcnow)
    preferred_date = StringField()  # Optional preferred date
    status = StringField(default='Requested', choices=['Requested', 'Scheduled', 'In-Process', 'Approved', 'Rejected', 'Completed Approved', 'Failed'])
    scheduled_date = StringField()
    assigned_inspector = StringField()
    admin_notes = StringField()
    inspector_report = StringField()
    inspector_report_file = FileField()

    meta = {
        'collection': 'inspection_requests'
    }

class Images(Document):
    college = StringField(required=True)
    classroom = ListField(DictField(), required=False)  # List of dictionaries for classrooms
    lab = ListField(DictField(), required=False)        # List of dictionaries for labs
    canteen = ListField(URLField(), required=False)    # Links for canteens
    pwd = ListField(URLField(), required=False)        # Links for PWD (accessible facilities)
    parking = ListField(URLField(), required=False)    # Links for parking areas
    washroom = ListField(URLField(), required=False)   # Links for washrooms

    meta = {
        'collection': 'images'
    }


