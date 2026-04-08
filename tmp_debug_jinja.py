import os
from jinja2 import Environment, FileSystemLoader

template_dir = r"c:\Users\91959\Documents\nearva phase 3\Nearva\templates"
env = Environment(loader=FileSystemLoader(template_dir))

try:
    template = env.get_template("worker_dashboard.html")
    print("Template loaded successfully.")
    # Attempt to render with dummy context
    template.render(request={}, worker_id="test_id")
    print("Template rendered successfully.")
except Exception as e:
    print(f"Error: {e}")
