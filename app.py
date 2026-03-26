import sqlite3
import os
from datetime import date, datetime, timedelta
from functools import wraps
from urllib.parse import urlencode

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from flask_bcrypt import Bcrypt

app = Flask(__name__)
app.secret_key = "segredo_muito_secreto"
bcrypt = Bcrypt(app)

QUADRA = {
    "slug": "society",
    "nome": "Quadra Society",
    "descricao": "Espaco para futebol society com reservas rapidas e agenda simples.",
    "admin_usuario": "admin",
    "admin_nome": "Administrador",
}

HORARIOS = [
    "08:00",
    "09:00",
    "10:00",
    "11:00",
    "12:00",
    "13:00",
    "14:00",
    "15:00",
    "16:00",
    "17:00",
    "18:00",
    "19:00",
    "20:00",
]

MESES_PT = [
    "janeiro",
    "fevereiro",
    "marco",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
]

SEMANA_PT = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]

STATUS_LABELS = {
    "pendente": "pendente",
    "confirmada": "concluido",
    "recusada": "recusado",
}


def get_conn():
    conn = sqlite3.connect("banco.db")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(cursor, table_name, column_name, definition):
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = {row["name"] for row in cursor.fetchall()}
    if column_name not in columns:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def format_date_pt(value):
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return value
    return f"{parsed.day:02d} de {MESES_PT[parsed.month - 1]} de {parsed.year}"


@app.template_filter("date_pt")
def date_pt_filter(value):
    return format_date_pt(value)


def normalize_date(value):
    try:
        selected = datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return date.today().isoformat()
    if selected < date.today():
        return date.today().isoformat()
    return selected.isoformat()


def build_calendar_days(total_days=14):
    today = date.today()
    days = []
    for offset in range(total_days):
        current = today + timedelta(days=offset)
        days.append(
            {
                "value": current.isoformat(),
                "weekday": SEMANA_PT[current.weekday()],
                "label": current.strftime("%d/%m"),
                "full_label": format_date_pt(current.isoformat()),
            }
        )
    return days


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT UNIQUE,
            senha TEXT,
            nome TEXT,
            telefone TEXT,
            tipo TEXT NOT NULL DEFAULT 'cliente'
        )
    """
    )
    ensure_column(c, "usuarios", "nome", "TEXT")
    ensure_column(c, "usuarios", "telefone", "TEXT")
    ensure_column(c, "usuarios", "tipo", "TEXT NOT NULL DEFAULT 'cliente'")

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS reservas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT,
            telefone TEXT,
            quadra TEXT,
            data_reserva TEXT,
            horario TEXT,
            usuario_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pendente',
            observacao_admin TEXT
        )
    """
    )
    ensure_column(c, "reservas", "status", "TEXT NOT NULL DEFAULT 'pendente'")
    ensure_column(c, "reservas", "observacao_admin", "TEXT")

    senha_hash = bcrypt.generate_password_hash("1234").decode("utf-8")
    c.execute("SELECT id FROM usuarios WHERE usuario = ?", (QUADRA["admin_usuario"],))
    admin = c.fetchone()
    if not admin:
        c.execute(
            """
            INSERT INTO usuarios (usuario, senha, nome, telefone, tipo)
            VALUES (?, ?, ?, ?, 'admin')
        """,
            (QUADRA["admin_usuario"], senha_hash, QUADRA["admin_nome"], ""),
        )
    else:
        c.execute(
            """
            UPDATE usuarios
            SET tipo = 'admin', nome = ?, senha = COALESCE(senha, ?)
            WHERE usuario = ?
        """,
            (QUADRA["admin_nome"], senha_hash, QUADRA["admin_usuario"]),
        )

    c.execute(
        """
        UPDATE reservas
        SET quadra = ?
        WHERE quadra IS NULL OR quadra = '' OR quadra != ?
    """,
        (QUADRA["nome"], QUADRA["nome"]),
    )

    conn.commit()
    conn.close()


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("usuario_id"):
            query = urlencode({"next": request.full_path.rstrip("?")})
            return redirect(f"{url_for('acesso')}?{query}")
        return view(*args, **kwargs)

    return wrapped_view


def customer_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("usuario_id"):
            return redirect(url_for("acesso"))
        if session.get("usuario_tipo") != "cliente":
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)

    return wrapped_view


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("usuario_id"):
            return redirect(url_for("acesso"))
        if session.get("usuario_tipo") != "admin":
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)

    return wrapped_view


def fetch_user(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM usuarios WHERE id = ?", (user_id,))
    user = c.fetchone()
    conn.close()
    return user


def get_public_info():
    today = date.today().isoformat()
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT COUNT(*) AS total
        FROM reservas
        WHERE data_reserva = ? AND status IN ('pendente', 'confirmada')
    """,
        (today,),
    )
    blocked = c.fetchone()["total"]
    conn.close()
    return {
        "nome": QUADRA["nome"],
        "descricao": QUADRA["descricao"],
        "livres_hoje": max(len(HORARIOS) - blocked, 0),
        "admin_nome": QUADRA["admin_nome"],
    }


def get_reserved_slots(data_reserva):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT horario
        FROM reservas
        WHERE data_reserva = ? AND status IN ('pendente', 'confirmada')
    """,
        (data_reserva,),
    )
    slots = {row["horario"] for row in c.fetchall()}
    conn.close()
    return slots


def build_slot_state(data_reserva):
    blocked = get_reserved_slots(data_reserva)
    return [{"horario": h, "disponivel": h not in blocked} for h in HORARIOS]


def is_slot_taken(data_reserva, horario):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT id
        FROM reservas
        WHERE data_reserva = ? AND horario = ? AND status IN ('pendente', 'confirmada')
    """,
        (data_reserva, horario),
    )
    taken = c.fetchone() is not None
    conn.close()
    return taken


def serialize_reservas(rows):
    items = []
    for row in rows:
        item = dict(row)
        item["data_formatada"] = format_date_pt(row["data_reserva"])
        item["status_label"] = STATUS_LABELS.get(row["status"], row["status"])
        item["whatsapp_link"] = (
            f"https://wa.me/55{row['telefone']}?text="
            f"Sua%20reserva%20na%20{QUADRA['nome'].replace(' ', '%20')}%20"
            f"para%20{format_date_pt(row['data_reserva']).replace(' ', '%20')}%20"
            f"as%20{row['horario']}%20esta%20como%20{STATUS_LABELS.get(row['status'], row['status']).replace(' ', '%20')}"
        )
        items.append(item)
    return items


@app.context_processor
def inject_user():
    return {"usuario_logado": session.get("usuario_nome"), "usuario_tipo": session.get("usuario_tipo")}


@app.route("/")
def index():
    return render_template("home.html", quadra=get_public_info())


@app.route("/acesso")
def acesso():
    if session.get("usuario_id"):
        return redirect(url_for("dashboard"))
    return render_template("login.html", quadra=get_public_info(), next_url=request.args.get("next", ""))


@app.route("/login", methods=["POST"])
def login():
    usuario = request.form.get("usuario", "").strip()
    senha = request.form.get("senha", "")
    tipo = request.form.get("tipo", "cliente")
    next_url = request.form.get("next_url", "")

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM usuarios WHERE usuario = ? AND tipo = ?", (usuario, tipo))
    user = c.fetchone()
    conn.close()

    if user and bcrypt.check_password_hash(user["senha"], senha):
        session["usuario_id"] = user["id"]
        session["usuario_nome"] = user["nome"] or user["usuario"]
        session["usuario_tipo"] = user["tipo"]
        if next_url:
            return redirect(next_url)
        return redirect(url_for("dashboard"))

    return render_template(
        "login.html",
        erro="Usuario ou senha invalidos.",
        active_tab=tipo,
        quadra=get_public_info(),
        next_url=next_url,
    )


@app.route("/cadastro", methods=["POST"])
def cadastro():
    nome = request.form.get("nome", "").strip()
    telefone = request.form.get("telefone", "").strip()
    usuario = request.form.get("usuario", "").strip()
    senha = request.form.get("senha", "")

    if not all([nome, telefone, usuario, senha]):
        return render_template(
            "login.html",
            erro_cadastro="Preencha todos os campos.",
            active_tab="cadastro",
            quadra=get_public_info(),
        )

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id FROM usuarios WHERE usuario = ?", (usuario,))
    if c.fetchone():
        conn.close()
        return render_template(
            "login.html",
            erro_cadastro="Esse usuario ja existe.",
            active_tab="cadastro",
            quadra=get_public_info(),
        )

    senha_hash = bcrypt.generate_password_hash(senha).decode("utf-8")
    c.execute(
        """
        INSERT INTO usuarios (usuario, senha, nome, telefone, tipo)
        VALUES (?, ?, ?, ?, 'cliente')
    """,
        (usuario, senha_hash, nome, telefone),
    )
    conn.commit()
    conn.close()

    return render_template(
        "login.html",
        sucesso_cadastro="Conta criada. Agora e so entrar.",
        active_tab="cliente",
        quadra=get_public_info(),
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required
def dashboard():
    if session.get("usuario_tipo") == "admin":
        return redirect(url_for("painel_admin"))
    return redirect(url_for("painel_cliente"))


@app.route("/painel-cliente")
@customer_required
def painel_cliente():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) AS total FROM reservas WHERE usuario_id = ?", (session["usuario_id"],))
    total = c.fetchone()["total"]
    c.execute(
        "SELECT COUNT(*) AS total FROM reservas WHERE usuario_id = ? AND status = 'pendente'",
        (session["usuario_id"],),
    )
    pendentes = c.fetchone()["total"]
    c.execute(
        "SELECT COUNT(*) AS total FROM reservas WHERE usuario_id = ? AND status = 'confirmada'",
        (session["usuario_id"],),
    )
    confirmadas = c.fetchone()["total"]
    c.execute(
        """
        SELECT *
        FROM reservas
        WHERE usuario_id = ?
        ORDER BY data_reserva, horario
        LIMIT 5
    """,
        (session["usuario_id"],),
    )
    reservas = serialize_reservas(c.fetchall())
    conn.close()

    stats = [
        {"label": "Pedidos", "value": total},
        {"label": "Pendentes", "value": pendentes},
        {"label": "Concluidos", "value": confirmadas},
    ]
    return render_template("dashboard_cliente.html", stats=stats, reservas=reservas, quadra=get_public_info())


@app.route("/painel-admin")
@admin_required
def painel_admin():
    filter_date = normalize_date(request.args.get("data", date.today().isoformat()))
    status_filter = request.args.get("status", "pendente")
    valid_status = {"pendente", "confirmada", "recusada", "todos"}
    if status_filter not in valid_status:
        status_filter = "pendente"

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) AS total FROM reservas")
    total = c.fetchone()["total"]
    c.execute("SELECT COUNT(*) AS total FROM reservas WHERE status = 'pendente'")
    pendentes_total = c.fetchone()["total"]
    c.execute(
        "SELECT COUNT(*) AS total FROM reservas WHERE status = 'confirmada' AND data_reserva = ?",
        (date.today().isoformat(),),
    )
    hoje = c.fetchone()["total"]
    query = """
        SELECT *
        FROM reservas
        WHERE data_reserva = ?
    """
    params = [filter_date]
    if status_filter != "todos":
        query += " AND status = ?"
        params.append(status_filter)
    query += " ORDER BY horario"
    c.execute(query, params)
    pendentes = serialize_reservas(c.fetchall())
    conn.close()

    stats = [
        {"label": "Pedidos", "value": total},
        {"label": "Pendentes", "value": pendentes_total},
        {"label": "Concluidos hoje", "value": hoje},
    ]
    return render_template(
        "dashboard_admin.html",
        stats=stats,
        pendentes=pendentes,
        quadra=get_public_info(),
        filtro_data=filter_date,
        filtro_status=status_filter,
        filtro_data_formatada=format_date_pt(filter_date),
    )


@app.route("/agendar", methods=["GET", "POST"])
@customer_required
def agendar():
    selected_date = normalize_date(request.values.get("data", date.today().isoformat()))
    current_user = fetch_user(session["usuario_id"])

    if request.method == "POST":
        horarios = [h for h in request.form.get("horarios", "").split(",") if h]
        horarios = list(dict.fromkeys(horarios))
        if not horarios:
            return render_template(
                "agendar.html",
                erro="Escolha um ou mais horarios disponiveis.",
                data=selected_date,
                calendario=build_calendar_days(),
                horarios=build_slot_state(selected_date),
                nome=current_user["nome"],
                telefone=current_user["telefone"],
                quadra=get_public_info(),
                data_formatada=format_date_pt(selected_date),
            )
        if any(h not in HORARIOS for h in horarios):
            return render_template(
                "agendar.html",
                erro="Foi encontrado um horario invalido.",
                data=selected_date,
                calendario=build_calendar_days(),
                horarios=build_slot_state(selected_date),
                nome=current_user["nome"],
                telefone=current_user["telefone"],
                quadra=get_public_info(),
                data_formatada=format_date_pt(selected_date),
            )
        occupied = [h for h in horarios if is_slot_taken(selected_date, h)]
        if occupied:
            return render_template(
                "agendar.html",
                erro=f"Os horarios {', '.join(occupied)} ja foram solicitados. Escolha outros.",
                data=selected_date,
                calendario=build_calendar_days(),
                horarios=build_slot_state(selected_date),
                nome=current_user["nome"],
                telefone=current_user["telefone"],
                quadra=get_public_info(),
                data_formatada=format_date_pt(selected_date),
            )

        conn = get_conn()
        c = conn.cursor()
        for horario in horarios:
            c.execute(
                """
                INSERT INTO reservas (nome, telefone, quadra, data_reserva, horario, usuario_id, status)
                VALUES (?, ?, ?, ?, ?, ?, 'pendente')
            """,
                (current_user["nome"], current_user["telefone"], QUADRA["nome"], selected_date, horario, session["usuario_id"]),
            )
        conn.commit()
        conn.close()
        return redirect(url_for("reservas"))

    return render_template(
        "agendar.html",
        data=selected_date,
        calendario=build_calendar_days(),
        horarios=build_slot_state(selected_date),
        nome=current_user["nome"],
        telefone=current_user["telefone"],
        quadra=get_public_info(),
        data_formatada=format_date_pt(selected_date),
    )


@app.route("/api/disponibilidade")
def disponibilidade():
    data_reserva = normalize_date(request.args.get("data", date.today().isoformat()))
    return jsonify(
        {
            "data": data_reserva,
            "data_formatada": format_date_pt(data_reserva),
            "quadra": QUADRA["nome"],
            "admin": QUADRA["admin_nome"],
            "horarios": build_slot_state(data_reserva),
        }
    )


@app.route("/reservas")
@login_required
def reservas():
    filter_date = normalize_date(request.args.get("data", date.today().isoformat()))
    status_filter = request.args.get("status", "todos")
    valid_status = {"pendente", "confirmada", "recusada", "todos"}
    if status_filter not in valid_status:
        status_filter = "todos"

    conn = get_conn()
    c = conn.cursor()
    if session.get("usuario_tipo") == "admin":
        query = "SELECT * FROM reservas WHERE data_reserva = ?"
        params = [filter_date]
        if status_filter != "todos":
            query += " AND status = ?"
            params.append(status_filter)
        query += " ORDER BY horario"
        c.execute(query, params)
    else:
        c.execute(
            "SELECT * FROM reservas WHERE usuario_id = ? ORDER BY data_reserva, horario",
            (session["usuario_id"],),
        )
    dados = serialize_reservas(c.fetchall())
    conn.close()
    return render_template(
        "reservas.html",
        reservas=dados,
        filtro_data=filter_date,
        filtro_status=status_filter,
        filtro_data_formatada=format_date_pt(filter_date),
    )


@app.route("/reserva/<int:reserva_id>/status", methods=["POST"])
@admin_required
def atualizar_status(reserva_id):
    novo_status = request.form.get("status")
    if novo_status not in {"confirmada", "recusada"}:
        return redirect(url_for("reservas"))

    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE reservas SET status = ? WHERE id = ?", (novo_status, reserva_id))
    conn.commit()
    conn.close()
    return redirect(url_for("reservas"))


@app.route("/cancelar/<int:reserva_id>", methods=["POST"])
@login_required
def cancelar(reserva_id):
    conn = get_conn()
    c = conn.cursor()
    if session.get("usuario_tipo") == "admin":
        c.execute("DELETE FROM reservas WHERE id = ?", (reserva_id,))
    else:
        c.execute("DELETE FROM reservas WHERE id = ? AND usuario_id = ?", (reserva_id, session["usuario_id"]))
    conn.commit()
    conn.close()
    return redirect(url_for("reservas"))

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
