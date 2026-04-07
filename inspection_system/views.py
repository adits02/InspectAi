from django.shortcuts import render, redirect


def index(request):
    """
    Home/Dashboard page for logged-in college users
    """

    # Get college name from session (set during login)
    college_name = request.session.get('college_name', 'Guest')

    return render(request, 'index.html', {
        'college_name': college_name
    })


def upload_certificate(request):
    """
    Certificate upload page
    """

    # Fetch college name safely from session
    college_name = request.session.get('college_name', 'Guest')

    return render(request, 'upload_certificate.html', {
        'college_name': college_name
    })
