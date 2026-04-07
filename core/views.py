# core/views.py
from django.shortcuts import render, redirect
from institute.models import certificate as Certificate, Images
from django.contrib import messages
from inspector.models import Feedback

from inspector.models import Feedback

#common
def homepage(request):
    return render(request, 'homepage.html')

def options(request):
    return render(request, 'options.html')



#aicte
def aicte_login(request):
    return render(request, 'aicte/aicte_login.html')

def aictemain(request):
    aicte = request.GET.get('aicte', 'Guest')
    return render(request, 'aicte/aictemain.html',{'aicte': aicte})

def aicte_institutes(request):
    return render(request, 'aicte/aicte_institutes.html')


def aicte_inspector(request):
    return render(request, 'aicte/aicte_inspector.html')


def aicte_annexure(request):
    return render(request, 'aicte/aicte_annexure.html')


def regionmap(request):
    return render(request, 'aicte/regionmap.html')


def region2(request):
    return render(request, 'aicte/region2.html')


def anamoly(request):
    return render(request, 'inspector/anamoly.html')




#college

def college_login(request):
    return render(request, 'institute/college_login.html')


def index(request):
    college_name = request.session.get('college_name')
    has_inspection_request = False
    inspection_status = None

    if college_name:
        from institute.models import InspectionRequest
        existing_request = InspectionRequest.objects(college_name=college_name).first()
        if existing_request:
            has_inspection_request = True
            inspection_status = existing_request.status

    return render(request, 'institute/index.html', {
        'has_inspection_request': has_inspection_request,
        'inspection_status': inspection_status
    })

def signup(request):
    return render(request, 'institute/signup.html')

def upload_certificate(request):
    college_name = request.session.get('college_name')
    if not college_name:
        messages.error(request, "Please login to access this page.")
        return redirect('college_login')
    existing_certificates = {}
    if college_name:
        certs = Certificate.objects(college_name=college_name)
        for cert in certs:
            existing_certificates[cert.field_name] = cert
    return render(request,'institute/upload_certificate.html', {
        'existing_certificates': existing_certificates
    })


def annexure(request):
    return render(request,'institute/annexure.html')

def upload_image(request):
    college_name = request.session.get('college_name')
    if not college_name:
        messages.error(request, "Please login to access this page.")
        return redirect('college_login')
    existing_images = {}
    if college_name:
        images_entry = Images.objects(college=college_name).first()
        if images_entry:
            existing_images = {
                'classroom': sum(len(d.get('url', [])) for d in images_entry.classroom) if images_entry.classroom else 0,
                'lab': sum(len(d.get('url', [])) for d in images_entry.lab) if images_entry.lab else 0,
                'canteen': len(images_entry.canteen) if images_entry.canteen else 0,
                'pwd': len(images_entry.pwd) if images_entry.pwd else 0,
                'parking': len(images_entry.parking) if images_entry.parking else 0,
                'washroom': len(images_entry.washroom) if images_entry.washroom else 0,
            }
    return render(request,'institute/upload_image.html', {
        'existing_images': existing_images
    })

def upload_excel(request):
    college_name = request.session.get('college_name')
    if not college_name:
        messages.error(request, "Please login to access this page.")
        return redirect('college_login')
    from institute.models import mandatory_dis, supporting_document
    existing_mandatory = None
    existing_supporting = {}
    if college_name:
        existing_mandatory = mandatory_dis.objects(college_name=college_name).first()
        for field in ['faculty_qualification', 'faculty_experience', 'student_admission', 'approval_affiliation', 'fire_noc']:
            doc = supporting_document.objects(college_name=college_name, field_name=field).first()
            if doc:
                existing_supporting[field] = doc
    return render(request,'institute/upload_excel.html', {
        'existing_mandatory': existing_mandatory,
        'existing_supporting': existing_supporting
    })

def classroom_upload(request):
    college_name = request.session.get('college_name')
    if not college_name:
        messages.error(request, "Please login to access this page.")
        return redirect('college_login')
    existing_count = 0
    images_entry = Images.objects(college=college_name).first()
    if images_entry and images_entry.classroom:
        existing_count = sum(len(d.get('url', [])) for d in images_entry.classroom)
    return render(request,'institute/classroom_upload.html', {'existing_count': existing_count})

def canteen_upload(request):
    college_name = request.session.get('college_name')
    if not college_name:
        messages.error(request, "Please login to access this page.")
        return redirect('college_login')
    existing_count = 0
    images_entry = Images.objects(college=college_name).first()
    if images_entry and images_entry.canteen:
        existing_count = len(images_entry.canteen)
    return render(request,'institute/canteen_upload.html', {'existing_count': existing_count})

def report3(request):
    return render(request,'institute/report3.html')








#inspector

def view_reports(request):
    user_id = request.GET.get('user_id', 'Guest')
    return render(request, 'inspector/view_reports.html',{'user_id': user_id})


def discussion_forum(request):
    return render(request, 'inspector/discussion_forum.html')

def view_feedback(request):
    college_name = request.session.get('college_name')  # Get college name from session
    if not college_name:
        messages.error(request, "College information is missing!")
        return redirect('college_login')  # Redirect to a default page if college name is not found

    # Retrieve feedback entries for the specific college
    feedback_entry = Feedback.objects(college_name=college_name)
    
    for feedback in feedback_entry:
            print(f"Inspector Name: {feedback.inspector_name}, College Name: {feedback.college_name}, Feedback: {feedback.feedback_text}")

    context = {
        'feedback_entry': feedback_entry,
        'college_name': college_name  # Pass college name to the template
    }
    return render(request, 'institute/feedback_view.html', context)  # Use full path to template

def inspector_login(request):
    return render(request, 'inspector/inspector_login.html')

def view_image(request):
    return render(request, 'inspector/view_image.html')

def annexure(request):
    return render(request,'inspector/annexure.html')

def report2(request):
    return render(request,'inspector/report2.html')


def feedback(request):
    return render(request,'inspector/feedback.html')


def pattern_pred(request):
    return render(request,'inspector/pattern_pred.html')



def view_classroom(request):
    return render(request,'inspector/view_classroom.html')


def view_lab(request):
    return render(request,'inspector/view_lab.html')


def view_washroom(request):
    return render(request,'inspector/view_washroom.html')


def view_parking(request):
    return render(request,'inspector/view_parking.html')


def view_pwd(request):
    return render(request,'inspector/view_pwd.html')


def view_canteen(request):
    return render(request,'inspector/view_canteen.html')

def lab_upload(request):
    college_name = request.session.get('college_name')
    if not college_name:
        messages.error(request, "Please login to access this page.")
        return redirect('college_login')
    existing_count = 0
    images_entry = Images.objects(college=college_name).first()
    if images_entry and images_entry.lab:
        existing_count = sum(len(d.get('url', [])) for d in images_entry.lab)
    return render(request,'institute/lab_upload.html', {'existing_count': existing_count})

def pwd_upload(request):
    college_name = request.session.get('college_name')
    if not college_name:
        messages.error(request, "Please login to access this page.")
        return redirect('college_login')
    existing_count = 0
    images_entry = Images.objects(college=college_name).first()
    if images_entry and images_entry.pwd:
        existing_count = len(images_entry.pwd)  # assuming pwd is list of urls, not dicts
    return render(request,'institute/pwd_upload.html', {'existing_count': existing_count})

def parking_upload(request):
    college_name = request.session.get('college_name')
    if not college_name:
        messages.error(request, "Please login to access this page.")
        return redirect('college_login')
    existing_count = 0
    images_entry = Images.objects(college=college_name).first()
    if images_entry and images_entry.parking:
        existing_count = len(images_entry.parking)
    return render(request,'institute/parking_upload.html', {'existing_count': existing_count})

def washroom_upload(request):
    college_name = request.session.get('college_name')
    if not college_name:
        messages.error(request, "Please login to access this page.")
        return redirect('college_login')
    existing_count = 0
    images_entry = Images.objects(college=college_name).first()
    if images_entry and images_entry.washroom:
        existing_count = len(images_entry.washroom)
    return render(request,'institute/washroom_upload.html', {'existing_count': existing_count})

def report3(request):
    # Fetch all feedback entries or filter as needed
    feedback_entries = Feedback.objects.all()

    # Pass data to the template
    context = {'feedback_entries': feedback_entries}
    return render(request, 'institute/report3.html', context)
