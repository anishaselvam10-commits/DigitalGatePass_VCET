# Digital Gate Pass
Velalar College of Engineering and Technology, Thindal

A web system where a student requests a gate pass, the mentor and HOD approve it, the student gets a digital pass with a QR code (and a printable PDF), and the gate guard verifies it and records exit and return times. Machine learning gives the mentor and HOD an advisory risk score. It never approves or rejects anything.

## Run it (Windows, Mac or Linux)

You need Python 3.10 or newer.

```
cd gatepass
python -m venv venv
venv\Scripts\activate          # on Mac/Linux: source venv/bin/activate
pip install -r requirements.txt
python seed.py --demo          # optional: adds sample staff, students and history
python app.py
```

Open http://127.0.0.1:5000

The first start takes a few seconds because it trains the two ML models. They are saved in `models/`.

## Using it with phones (needed for QR scanning)

The QR on each pass is a short link to this app. A phone camera can only open it if the phone can reach your laptop, so:

1. Connect the laptop and the phones to the **same Wi-Fi** (a phone hotspot works too).
2. Find the laptop's Wi-Fi address. Windows: run `ipconfig` and look for "IPv4 Address" (for example 192.168.1.20). Mac/Linux: `ifconfig` or `ip a`.
3. Start the app so other devices may connect, then open it **using that address, not localhost**:

```
set HOST=0.0.0.0               # Mac/Linux: export HOST=0.0.0.0
python app.py
```

Open `http://192.168.1.20:5000` (your own address) on the laptop and on the phones. If Windows asks about the firewall, choose Allow on private networks.

4. Better: open `config.py` and set `PUBLIC_URL = "http://192.168.1.20:5000"` so every QR always contains that address. Passes already printed keep the address they were made with, so regenerate them after changing it.

If a pass page shows a red warning that the QR points to `127.0.0.1` or `localhost`, a phone cannot open it. Fix it as above.

**Guard on a phone:** log in as the gate account on the phone once, then scan any pass with the normal camera app and tap the link. The verify result appears directly, with the Record exit / Record return button. If the guard is not logged in, the link asks for login and then continues to the result. Only gate and admin accounts can open these links, so a student scanning their own QR just gets "no access".

**Printed or PDF passes:** the QR is large and has error correction, so it scans from a screen or from paper. If the guard's camera app does not offer the link, the guard can type the code under the QR (like `GP-00041-68F2EA3868`) into the Verify box.

## Logins

Everyone logs in with **register number / staff ID + date of birth**.

| Role | Register / ID | Date of birth |
|---|---|---|
| Admin | ADMIN | 2000-01-01 (change it first, see below) |
| HOD (demo) | H001 | 1978-07-01 |
| Mentor (demo) | M001 | 1985-03-12 |
| Gate guard (demo) | G001 | 1990-01-15 |
| Student with history (demo) | 21CS001 | 2004-05-10 |
| Student with history (demo) | 21CS002 | 2004-08-21 |
| Student with no history (demo) | 21CS006 | 2004-06-18 |

The demo rows exist only if you ran `python seed.py --demo`. For real use, skip it and add real users from the Admin page.

## Real use, first steps

1. Log in as ADMIN, open Users, edit ADMIN and change the date of birth.
2. Add HODs and mentors (Admin > Add a user), then the gate guard.
3. Students either sign up themselves (they pick their mentor and HOD) or you add them. Set `SELF_SIGNUP_STUDENTS = False` in `config.py` to allow only admin-created students.

## Workflow

Student applies → Mentor approves or rejects → HOD approves or rejects → pass is ready (QR + PDF) → guard verifies the QR, records **exit** → guard records **return** → system marks it On time or Late.

Each pass is valid for one date only. The QR holds a link containing the pass ID and a signed code (HMAC with a secret key stored in `instance/secret.key`). The date and register number are covered by the signature, so an edited or invented pass fails verification.

## Settings (`config.py`)

Everything you may want to change is in this one file:

- College name and place, list of departments
- Earliest leaving time (08:00) and latest return time (**15:30**)
- Longest pass (8 hours) and passes per day (2)
- `HOD_SKIP_UNDER_HOURS`: set to e.g. `2` so short passes need only the mentor. `0` means the HOD always approves.
- Late-return risk thresholds, minimum history before ML scores show (3 completed passes)

## The machine learning part

Both models are advisory and only shown to the mentor and HOD.

- **Random Forest classifier**: predicts the probability that this pass ends with a **late return**, shown as Low, Medium or High risk.
- **Isolation Forest**: flags a request as **Unusual** if it differs from normal requests (very long, odd hour, many requests in 30 days, far from the student's usual length).

Features come from the student's own history: number of completed passes, late-return ratio, rejection ratio, average pass length, requested length, difference from average, leaving hour, return hour, requests in the last 30 days.

Students with fewer than 3 completed passes show **Insufficient History**.

**Important, and say this honestly in your viva:** a new college has no history, so the models are first trained on synthetic students generated in `ml.py`. The accuracy figure on the Admin page measures the pipeline on that synthetic data and says nothing about real students. After a few weeks of real use, press **Retrain with real data** on the Admin page (or run `python train_models.py`). Real completed passes are then added to the training set.

## Project files

```
app.py            routes: login, student, mentor/HOD, gate, admin, pass page, QR, PDF
config.py         all settings
db.py             SQLite schema and helpers
ml.py             features, synthetic data, training, scoring
train_models.py   retrain from the command line
seed.py           admin account and optional demo data
templates/        HTML pages
static/style.css  styling
instance/         created at first run: database and secret key (do not share)
models/           created at first run: trained models
```

Database: SQLite file `instance/gatepass.db`. Two tables, `users` and `passes`. To start fresh, stop the app and delete the `instance` folder.

## Security notes

- Date of birth is easy to learn about a classmate. It is the login you asked for, but if this is ever used for real at the college, add a password or OTP. The app locks a register number for 5 minutes after 5 wrong attempts and uses CSRF protection on every form.
- Keep `instance/secret.key` private. If it leaks, people could forge passes. Deleting it invalidates all existing passes.
- `python app.py` uses Flask's built-in server, which is fine for a demo or a small campus pilot. For a bigger deployment use a production server (for example gunicorn or waitress) behind HTTPS.

## Not included

- No email or SMS alerts. Scanning uses the phone's own camera app (which opens the link), or a USB barcode scanner, or typing the code. There is no built-in scanner inside the web page.
