import os, sqlite3
from io import BytesIO
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, abort
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "edusuivi.db")
UPLOAD = os.path.join(BASE, "uploads")
os.makedirs(UPLOAD, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("EDUSUIVI_SECRET", "CHANGE-ME-IN-PRODUCTION")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
login_manager = LoginManager(app)
login_manager.login_view = "login"

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
      email TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
      role TEXT NOT NULL CHECK(role IN ('admin','teacher','parent'))
    );
    CREATE TABLE IF NOT EXISTS classes(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, photo TEXT
    );
    CREATE TABLE IF NOT EXISTS students(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
      birth_date TEXT, class_id INTEGER, parent_id INTEGER,
      photo TEXT, FOREIGN KEY(class_id) REFERENCES classes(id),
      FOREIGN KEY(parent_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS subjects(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE
    );
    CREATE TABLE IF NOT EXISTS grades(
      id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER,
      subject_id INTEGER, teacher_id INTEGER, value REAL,
      UNIQUE(student_id,subject_id), FOREIGN KEY(student_id) REFERENCES students(id),
      FOREIGN KEY(subject_id) REFERENCES subjects(id)
    );
    CREATE TABLE IF NOT EXISTS observations(
      id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER,
      teacher_id INTEGER, text TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS posts(
      id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, body TEXT,
      author_id INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)
    if not con.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
        con.execute("INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)",
                    ("Administrateur","admin@edusuivi.ml",generate_password_hash("Admin@12345"),"admin"))
    for c in ["6e A","5e A","4e A","3e A"]:
        con.execute("INSERT OR IGNORE INTO classes(name) VALUES(?)",(c,))
    for s in ["Mathématiques","Français","Anglais","Sciences","Histoire-Géographie"]:
        con.execute("INSERT OR IGNORE INTO subjects(name) VALUES(?)",(s,))
    con.commit(); con.close()

@login_manager.user_loader
def load_user(uid):
    con=db(); u=con.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone(); con.close()
    if not u: return None
    obj=UserMixin(); obj.id=u["id"]; obj.name=u["name"]; obj.email=u["email"]; obj.role=u["role"]; return obj

def role_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapped(*a,**kw):
            if current_user.role not in roles: abort(403)
            return fn(*a,**kw)
        return wrapped
    return deco

@app.context_processor
def inject():
    return {"school_name":"EduSuivi Mali"}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/register/<role>", methods=["GET","POST"])
def register(role):
    if role not in ("parent","teacher"): abort(404)
    if request.method=="POST":
        name=request.form["name"].strip(); email=request.form["email"].strip().lower()
        password=request.form["password"]
        if len(password)<8: flash("Le mot de passe doit contenir au moins 8 caractères."); return redirect(request.url)
        con=db()
        try:
            con.execute("INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)",
                        (name,email,generate_password_hash(password),role)); con.commit()
            flash("Inscription réussie. Vous pouvez maintenant vous connecter.")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Cette adresse e-mail est déjà utilisée.")
        finally: con.close()
    return render_template("register.html", role=role)

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        email=request.form["email"].strip().lower(); password=request.form["password"]
        con=db(); u=con.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone(); con.close()
        if u and check_password_hash(u["password"],password):
            login_user(type("U",(UserMixin,),{})())
            # Re-login with proper object
            obj=load_user(u["id"]); logout_user(); login_user(obj)
            return redirect(url_for("dashboard"))
        flash("E-mail ou mot de passe incorrect.")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user(); return redirect(url_for("index"))

@app.route("/dashboard")
@login_required
def dashboard():
    con=db()
    classes=con.execute("SELECT * FROM classes ORDER BY name").fetchall()
    subjects=con.execute("SELECT * FROM subjects ORDER BY name").fetchall()
    if current_user.role=="parent":
        students=con.execute("SELECT s.*,c.name class_name FROM students s LEFT JOIN classes c ON c.id=s.class_id WHERE parent_id=?",(current_user.id,)).fetchall()
    else:
        students=con.execute("SELECT s.*,c.name class_name FROM students s LEFT JOIN classes c ON c.id=s.class_id ORDER BY s.name").fetchall()
    posts=con.execute("SELECT p.*,u.name author FROM posts p JOIN users u ON u.id=p.author_id ORDER BY p.id DESC").fetchall()
    con.close()
    return render_template("dashboard.html",classes=classes,subjects=subjects,students=students,posts=posts)

@app.route("/student/add", methods=["POST"])
@login_required
@role_required("admin")
def add_student():
    con=db(); con.execute("INSERT INTO students(name,birth_date,class_id,parent_id) VALUES(?,?,?,?)",
      (request.form["name"],request.form.get("birth_date"),request.form.get("class_id") or None,request.form.get("parent_id") or None))
    con.commit(); con.close(); return redirect(url_for("dashboard"))

@app.route("/grade/add", methods=["POST"])
@login_required
@role_required("teacher","admin")
def add_grade():
    con=db()
    con.execute("""INSERT INTO grades(student_id,subject_id,teacher_id,value) VALUES(?,?,?,?)
                   ON CONFLICT(student_id,subject_id) DO UPDATE SET value=excluded.value,teacher_id=excluded.teacher_id""",
                (request.form["student_id"],request.form["subject_id"],current_user.id,float(request.form["value"])))
    con.commit(); con.close(); flash("Note enregistrée."); return redirect(url_for("dashboard"))

@app.route("/observation/add", methods=["POST"])
@login_required
@role_required("teacher","admin")
def add_observation():
    con=db(); con.execute("INSERT INTO observations(student_id,teacher_id,text) VALUES(?,?,?)",
                          (request.form["student_id"],current_user.id,request.form["text"]))
    con.commit(); con.close(); return redirect(url_for("dashboard"))

@app.route("/post/add", methods=["POST"])
@login_required
@role_required("teacher","admin")
def add_post():
    con=db(); con.execute("INSERT INTO posts(title,body,author_id) VALUES(?,?,?)",
                          (request.form["title"],request.form["body"],current_user.id))
    con.commit(); con.close(); return redirect(url_for("dashboard"))

@app.route("/search")
@login_required
def search():
    q=request.args.get("q","").strip()
    con=db()
    if current_user.role=="parent":
        rows=con.execute("""SELECT s.*,c.name class_name FROM students s LEFT JOIN classes c ON c.id=s.class_id
                            WHERE s.parent_id=? AND s.name LIKE ?""",(current_user.id,f"%{q}%")).fetchall()
    else:
        rows=con.execute("""SELECT s.*,c.name class_name FROM students s LEFT JOIN classes c ON c.id=s.class_id
                            WHERE s.name LIKE ? OR c.name LIKE ?""",(f"%{q}%",f"%{q}%")).fetchall()
    con.close(); return render_template("search.html",students=rows,q=q)

@app.route("/bulletin/<int:sid>.pdf")
@login_required
def bulletin(sid):
    con=db(); s=con.execute("""SELECT s.*,c.name class_name FROM students s LEFT JOIN classes c ON c.id=s.class_id
                               WHERE s.id=?""",(sid,)).fetchone()
    if not s: abort(404)
    if current_user.role=="parent" and s["parent_id"]!=current_user.id: abort(403)
    grades=con.execute("""SELECT sub.name, g.value FROM grades g JOIN subjects sub ON sub.id=g.subject_id
                          WHERE g.student_id=? ORDER BY sub.name""",(sid,)).fetchall()
    obs=con.execute("""SELECT o.text,u.name FROM observations o JOIN users u ON u.id=o.teacher_id
                       WHERE o.student_id=? ORDER BY o.id DESC""",(sid,)).fetchall()
    con.close()
    buf=BytesIO(); c=canvas.Canvas(buf,pagesize=A4); w,h=A4
    c.setFont("Helvetica-Bold",20); c.drawString(55,h-65,"EDUSUIVI MALI")
    c.setFont("Helvetica",11); c.drawString(55,h-85,"Bulletin scolaire")
    c.line(55,h-95,w-55,h-95)
    y=h-125; c.setFont("Helvetica-Bold",12); c.drawString(55,y,"Élève :"); c.setFont("Helvetica",12); c.drawString(115,y,s["name"])
    y-=20; c.drawString(55,y,"Classe :"); c.drawString(115,y,s["class_name"] or "Non affectée")
    y-=40; c.setFont("Helvetica-Bold",11); c.drawString(55,y,"Matière"); c.drawString(360,y,"Note /20"); c.line(55,y-7,w-55,y-7); y-=28
    total=0
    for g in grades:
        c.setFont("Helvetica",11); c.drawString(55,y,g["name"]); c.drawString(360,y,f'{g["value"]:.2f}'); total+=g["value"]; y-=23
    avg=total/len(grades) if grades else 0
    y-=10; c.setFont("Helvetica-Bold",12); c.drawString(55,y,f"Moyenne générale : {avg:.2f}/20")
    y-=40; c.drawString(55,y,"Observations :"); y-=20; c.setFont("Helvetica",10)
    for o in obs[:6]:
        text=o["text"]; c.drawString(65,y,"• "+text[:105]); y-=17
    c.setFont("Helvetica-Oblique",9); c.drawString(55,45,"Document généré par EduSuivi Mali")
    c.save(); buf.seek(0)
    return send_file(buf,as_attachment=True,download_name=f"bulletin_{sid}.pdf",mimetype="application/pdf")

@app.route("/photo/class/<int:cid>", methods=["POST"])
@login_required
@role_required("admin")
def class_photo(cid):
    f=request.files.get("photo")
    if f and f.filename:
        name=secure_filename(f.filename); path=os.path.join(UPLOAD,f"class_{cid}_{name}"); f.save(path)
        con=db(); con.execute("UPDATE classes SET photo=? WHERE id=?",(os.path.basename(path),cid)); con.commit(); con.close()
    return redirect(url_for("dashboard"))

if __name__=="__main__":
    init_db()
    app.run(debug=True, host="127.0.0.1", port=5000)
