"""Settings for the Digital Gate Pass system. Edit this file to change college details and pass rules."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
DB_PATH = os.path.join(INSTANCE_DIR, "gatepass.db")
MODEL_DIR = os.path.join(BASE_DIR, "models")

# ---- College details (shown on every page and on the printed pass) ----
COLLEGE_NAME = "Velalar College of Engineering and Technology"
COLLEGE_PLACE = "Thindal, Erode"
DEPARTMENTS = ["CSE", "ECE", "EEE", "MECH", "CIVIL", "IT", "AIDS", "AIML", "CYBER SECURITY"]

# Address that phones use to reach this app, e.g. "http://192.168.1.20:5000". It goes inside the QR code, so a phone
# camera that scans a pass opens the verify page. Leave empty to use whatever address the pass page was opened with.
PUBLIC_URL = "http://192.168.1.5:5000"

# ---- Pass rules ----
EARLIEST_LEAVE = "08:00"        # no pass can start before this time
LATEST_RETURN = "15:30"         # every pass must end by this time (3:30 PM)
MAX_HOURS = 8                   # longest allowed pass (hours)
MAX_PASSES_PER_DAY = 2          # per student, counting pending and approved passes
HOD_SKIP_UNDER_HOURS = 0        # e.g. 2 = passes shorter than 2 hours need only mentor approval. 0 = HOD always approves.

# ---- Accounts ----
SELF_SIGNUP_STUDENTS = True     # students may create their own account. Mentors, HODs, guards are added by the admin.
# Login is register number / staff ID + date of birth.
ADMIN_REG = "ADMIN"
ADMIN_DEFAULT_DOB = "2000-01-01"      # CHANGE THIS after the first login (Admin > Users > ADMIN)
MAX_LOGIN_FAILURES = 5          # failed attempts before a 5 minute lockout

# ---- Machine learning (advisory only, never approves or rejects) ----
MIN_HISTORY = 3                 # completed passes needed before scores are shown
RISK_LOW_BELOW = 0.25           # probability of late return below this = Low Risk
RISK_MEDIUM_BELOW = 0.50        # below this = Medium Risk, otherwise High Risk
