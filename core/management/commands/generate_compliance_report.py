from django.core.management.base import BaseCommand
import requests
import sys

class Command(BaseCommand):
    help = 'Generate compliance report for a college'

    def add_arguments(self, parser):
        parser.add_argument('college_name', type=str, help='College name')
        parser.add_argument('college_intake', type=str, help='Student intake')

    def handle(self, *args, **options):
        college_name = options['college_name']
        college_intake = options['college_intake']
        
        self.stdout.write(f"Generating compliance report for {college_name} with intake {college_intake}...")
        
        try:
            # Call FastAPI endpoint
            response = requests.post(
                "http://localhost:8001/create-compliance-report/",
                json={"college_name": college_name, "college_intake": college_intake},
                timeout=60
            )
            
            if response.status_code == 200:
                self.stdout.write(self.style.SUCCESS(f'✓ Compliance report generated successfully'))
                self.stdout.write(str(response.json()))
            else:
                self.stdout.write(self.style.ERROR(f'✗ Error: {response.text}'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Failed to generate report: {str(e)}'))
            self.stdout.write(self.style.WARNING('Make sure FastAPI is running on port 8001'))
            sys.exit(1)
