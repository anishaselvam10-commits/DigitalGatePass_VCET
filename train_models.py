"""Train (or retrain) the Random Forest and Isolation Forest.

    python train_models.py

Uses synthetic students plus any real completed passes already in the database.
Run it again after a few weeks of real use. The admin page has the same button.
"""
import db
import ml


def main():
    db.init_db()
    conn = db.connect()
    by_student = {}
    for r in conn.execute("SELECT * FROM passes ORDER BY id"):
        by_student.setdefault(r["student_id"], []).append(dict(r))
    conn.close()
    meta = ml.train(ml.rows_from_passes(by_student))
    print("Models saved in the models/ folder.")
    for k, v in meta.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
