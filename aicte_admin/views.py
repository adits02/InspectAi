import io

from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import Http404, JsonResponse, FileResponse, HttpResponseNotFound

from mongoengine.errors import DoesNotExist
from bson import ObjectId

from .models import AICTEUser
from .forms import InspectorForm

from inspector.models import Inspector
from institute.models import College, InspectionRequest


def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('aicte-user')
        password = request.POST.get('login-pass')

        try:
            user = AICTEUser.objects.get(
                aicte_user=username,
                password=password
            )
            request.session['aicte'] = user.aicte_user
            messages.success(request, 'Login successful!')
            return redirect('aictemain')

        except DoesNotExist:
            messages.error(request, 'Invalid credentials, please try again.')
            return redirect('aicte_login')

    return render(request, 'aicte_login.html')


def aicte_logout(request):
    request.session.flush()
    return render(request, 'options.html')


def inspector_list(request):
    inspectors = Inspector.objects.all()
    colleges = College.objects.all()
    return render(
        request,
        'aicte/aicte_inspector.html',
        {
            'inspectors': inspectors,
            'colleges': colleges
        }
    )


def inspector_create(request):
    if request.method == 'POST':
        form = InspectorForm(request.POST)
        if form.is_valid():
            inspector = Inspector(
                user_id=form.cleaned_data['user_id'],
                password=form.cleaned_data['password'],
                college=""
            )
            inspector.save()
            return redirect('inspector_list')
    else:
        form = InspectorForm()

    return render(request, 'aicte/aicte_inspector.html', {'form': form})


def inspector_detail(request, pk):
    try:
        inspector = Inspector.objects.get(id=pk)
        return JsonResponse({
            'user_id': inspector.user_id,
            'password': inspector.password
        })
    except Inspector.DoesNotExist:
        return JsonResponse({'error': 'Inspector not found'}, status=404)


def inspector_update(request, pk):
    try:
        inspector = Inspector.objects.get(id=pk)
    except Inspector.DoesNotExist:
        raise Http404("Inspector does not exist")

    colleges = College.objects.all()

    if request.method == 'POST':
        inspector.user_id = request.POST.get('user_id')
        inspector.password = request.POST.get('password')
        inspector.college = request.POST.get('college')
        inspector.save()
        return redirect('inspector_list')

    return render(
        request,
        'aicte/aicte_inspector_edit.html',
        {
            'inspector': inspector,
            'colleges': colleges
        }
    )


def inspector_delete(request, pk):
    try:
        inspector = Inspector.objects.get(id=pk)
        inspector.delete()
        return redirect('inspector_list')
    except Inspector.DoesNotExist:
        raise Http404("Inspector does not exist")


def institute_list(request):
    institutes = College.objects.all()
    return render(
        request,
        'aicte/aicte_institutes.html',
        {'institutes': institutes}
    )


def institute_detail(request, pk):
    try:
        institute = College.objects.get(id=ObjectId(pk))
    except College.DoesNotExist:
        raise Http404("Institute does not exist")

    return render(
        request,
        'aicte/aicte_institutes.html',
        {'institute': institute}
    )


def institute_update(request, pk):
    try:
        institute = College.objects.get(id=ObjectId(pk))
    except College.DoesNotExist:
        raise Http404("Institute does not exist")

    if request.method == 'POST':
        institute.college_name = request.POST.get('college_name')
        institute.college_code = request.POST.get('college_code')
        institute.email = request.POST.get('email')
        institute.pin_id = request.POST.get('pin_id')
        institute.state = request.POST.get('state')
        institute.city = request.POST.get('city')
        institute.approved = request.POST.get('approved')
        institute.save()
        return redirect('institute_list')

    return render(
        request,
        'aicte/aicte_institutes.html',
        {'institute': institute}
    )


def inspection_report(request):
    from inspector.models import Feedback, compliancereport
    from institute.models import College
    from django.http import HttpResponse
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from io import BytesIO
    from datetime import datetime

    if request.method == 'POST':
        # Get filters
        start_date = request.POST.get('start_date')
        end_date = request.POST.get('end_date')
        region = request.POST.get('region')  # state

        # Get inspection request objects directly (all status types)
        inspection_query = InspectionRequest.objects.all().order_by('-requested_date')

        if start_date:
            start_dt = datetime.fromisoformat(start_date).replace(hour=0, minute=0, second=0, microsecond=0)
            inspection_query = inspection_query.filter(requested_date__gte=start_dt)
        if end_date:
            end_dt = datetime.fromisoformat(end_date).replace(hour=23, minute=59, second=59, microsecond=999999)
            inspection_query = inspection_query.filter(requested_date__lte=end_dt)

        requests = []
        status_counts = {
            'Requested': 0,
            'Approved': 0,
            'Scheduled': 0,
            'In-Process': 0,
            'Completed Approved': 0,
            'Failed': 0,
            'Rejected': 0
        }

        for req in inspection_query:
            try:
                college = College.objects.get(college_name=req.college_name)
                if region and college.state != region:
                    continue

                requests.append({
                    'college_name': req.college_name,
                    'state': college.state,
                    'city': college.city,
                    'status': req.status,
                    'requested_date': req.requested_date,
                    'scheduled_date': req.scheduled_date,
                    'assigned_inspector': req.assigned_inspector,
                    'admin_notes': req.admin_notes
                })

                if req.status in status_counts:
                    status_counts[req.status] += 1
                else:
                    status_counts[req.status] = 1
            except College.DoesNotExist:
                continue

        # Generate PDF
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        elements = []
        styles = getSampleStyleSheet()

        # Title
        title = Paragraph("Inspection Requests Report", styles['Title'])
        elements.append(title)
        elements.append(Paragraph(" ", styles['Normal']))

        # Status summary
        summary_text = "Inspection Requests by Status: "
        summary_text += ", ".join([f"{k}: {v}" for k, v in status_counts.items()])
        elements.append(Paragraph(summary_text, styles['Normal']))
        elements.append(Paragraph(" ", styles['Normal']))

        # Table headers
        header_style = ParagraphStyle(name='table_header', parent=styles['Heading5'], alignment=1, fontSize=10, leading=12)
        body_style = ParagraphStyle(name='table_body', parent=styles['Normal'], fontSize=8, leading=10)

        data = [[
            Paragraph('Institution', header_style),
            Paragraph('State', header_style),
            Paragraph('City', header_style),
            Paragraph('Status', header_style),
            Paragraph('Requested Date', header_style),
            Paragraph('Scheduled Date', header_style),
            Paragraph('Inspector', header_style)
        ]]

        for r in requests:
            data.append([
                Paragraph(r['college_name'] or '-', body_style),
                Paragraph(r['state'] or '-', body_style),
                Paragraph(r['city'] or '-', body_style),
                Paragraph(r['status'] or '-', body_style),
                Paragraph(r['requested_date'].strftime('%Y-%m-%d %H:%M') if r['requested_date'] else '-', body_style),
                Paragraph(r['scheduled_date'] or '-', body_style),
                Paragraph(r['assigned_inspector'] or '-', body_style)
            ])

        if len(data) == 1:
            data.append([Paragraph('No requests found', body_style), '', '', '', '', '', ''])

        col_widths = [120, 60, 60, 80, 90, 90, 92]
        table = Table(data, colWidths=col_widths, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('WORDWRAP', (0, 0), (-1, -1), 'CJK')
        ]))

        elements.append(table)

        doc.build(elements)
        buffer.seek(0)

        response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="inspection_report.pdf"'
        return response

        # Generate PDF
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        elements = []
        styles = getSampleStyleSheet()

        # Title
        title = Paragraph("Inspected Institutions Report", styles['Title'])
        elements.append(title)
        elements.append(Paragraph(" ", styles['Normal']))

        # Filters info
        filter_info = f"Filters applied: "
        if start_date:
            filter_info += f"From {start_date} "
        if end_date:
            filter_info += f"To {end_date} "
        if region:
            filter_info += f"Region: {region}"
        if not start_date and not end_date and not region:
            filter_info += "None"
        
        elements.append(Paragraph(filter_info, styles['Normal']))
        elements.append(Paragraph(" ", styles['Normal']))

        # Table
        data = [['Institution Name', 'State', 'City', 'Email']]
        for college in sorted(colleges, key=lambda x: x['name']):
            data.append([college['name'], college['state'], college['city'], college['email']])

        table = Table(data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 14),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ]))

        elements.append(table)

        doc.build(elements)
        buffer.seek(0)

        response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="inspection_report.pdf"'
        return response

    # GET request - show form
    states = list(College.objects.distinct('state'))
    return render(request, 'aicte/inspection_report.html', {'states': states})


def inspection_requests(request):
    requests = InspectionRequest.objects(status='Requested').order_by('-requested_date')
    inspectors = Inspector.objects.all()
    return render(request, 'aicte/inspection_requests.html', {
        'requests': requests,
        'inspectors': inspectors
    })


def inspector_reports(request):
    # Show scheduled and inspection report review workflows
    requests = InspectionRequest.objects(status__in=['Scheduled', 'In-Process', 'Approved', 'Failed', 'Completed Approved', 'Rejected']).order_by('-requested_date')
    inspectors = Inspector.objects.all()
    return render(request, 'aicte/inspector_reports.html', {
        'requests': requests,
        'inspectors': inspectors
    })


def receive_inspection_schedule(request, request_id):
    try:
        inspection_request = InspectionRequest.objects.get(id=request_id)
        inspection_request.status = 'In-Process'
        inspection_request.save()
    except InspectionRequest.DoesNotExist:
        pass
    return redirect('inspector_reports')


def schedule_inspection(request, request_id):
    try:
        inspection_request = InspectionRequest.objects.get(id=request_id)
    except InspectionRequest.DoesNotExist:
        raise Http404("Inspection request not found")

    if request.method == 'POST':
        scheduled_date = request.POST.get('scheduled_date')
        assigned_inspector = request.POST.get('assigned_inspector')
        admin_notes = request.POST.get('admin_notes')

        inspection_request.status = 'Scheduled'
        inspection_request.scheduled_date = scheduled_date
        inspection_request.assigned_inspector = assigned_inspector
        inspection_request.admin_notes = admin_notes
        inspection_request.save()

        return redirect('inspection_requests')

    inspectors = Inspector.objects.all()
    return render(request, 'aicte/schedule_inspection.html', {
        'request': inspection_request,
        'inspectors': inspectors
    })


def reject_inspection_request(request, request_id):
    try:
        inspection_request = InspectionRequest.objects.get(id=request_id)
        inspection_request.status = 'Rejected'
        inspection_request.save()
    except InspectionRequest.DoesNotExist:
        pass
    return redirect('inspection_requests')


def download_inspection_report_admin(request, request_id):
    try:
        inspection_request = InspectionRequest.objects.get(id=request_id)
        report_file = inspection_request.inspector_report_file
        if not report_file:
            return HttpResponseNotFound('No report file found for this inspection request')

        if hasattr(report_file, 'read'):
            raw = report_file.read()
        elif hasattr(report_file, 'stream'):
            raw = report_file.stream.read()
        else:
            raw = report_file

        if not raw:
            return HttpResponseNotFound('Report file is empty')

        return FileResponse(io.BytesIO(raw), as_attachment=True, filename=f'InspectionReport_{inspection_request.college_name}.pdf')
    except InspectionRequest.DoesNotExist:
        return HttpResponseNotFound('Inspection request not found')
    except Exception as e:
        print(f"download_inspection_report_admin error: {e}")
        return HttpResponseNotFound('Error retrieving report file')


def approve_inspection_request(request, request_id):
    try:
        inspection_request = InspectionRequest.objects.get(id=request_id)
        inspection_request.status = 'Approved'
        inspection_request.save()
    except InspectionRequest.DoesNotExist:
        pass
    return redirect('inspection_requests')


def complete_inspection(request, request_id):
    try:
        inspection_request = InspectionRequest.objects.get(id=request_id)
        result = request.GET.get('result', 'approved')
        if result == 'failed':
            inspection_request.status = 'Failed'
        else:
            inspection_request.status = 'Completed Approved'
        inspection_request.save()
    except InspectionRequest.DoesNotExist:
        pass
    return redirect('inspection_requests')
