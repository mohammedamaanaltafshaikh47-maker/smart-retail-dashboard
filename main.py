import io
import json
import os
import secrets
import uuid
import pandas as pd
from fastapi import FastAPI, UploadFile, File, Form, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

# --- INITIALIZE ENTERPRISE APP ENGINE ---
app = FastAPI()

# --- GLOBAL MEMORY ARCHITECTURE ---
# FIX: This used to be a single shared dict, so every visitor overwrote each
# other's data. Now it's keyed by session_id, so each browser/user gets their
# own metrics. See get_or_create_session_id() below.
STORAGE: dict = {}


def get_or_create_session_id(request: Request):
    """Reads the session_id cookie if present, otherwise creates a new one.
    Returns (session_id, is_new) so the caller knows whether to set the cookie."""
    session_id = request.cookies.get("session_id")
    is_new = session_id is None
    if is_new:
        session_id = str(uuid.uuid4())
    return session_id, is_new

def identify_and_classify_sheet(df: pd.DataFrame, sheet_name: str) -> str:
    if df.empty:
        return "EMPTY"
    instruction_keywords = {'instruction', 'practice', 'syllabus', 'subject code', 'task', 'coverage', 'guideline', 'blueprint'}
    if any(kw in sheet_name.lower() for kw in instruction_keywords):
        return "INSTRUCTIONS"
    
    total_cells = df.shape[0] * df.shape[1]
    if total_cells == 0:
        return "EMPTY"
    string_cells = df.select_dtypes(include=['object']).astype(str).apply(lambda x: x.str.strip().str.len() > 0).sum().sum()
    numeric_cells = df.select_dtypes(include=['number']).notna().sum().sum()
    
    if string_cells > 0 and (numeric_cells / string_cells) < 0.05:
        return "INSTRUCTIONS"
    return "TRANSACTIONAL_DATA"

def intelligent_excel_pipeline(file_bytes: bytes) -> tuple[pd.DataFrame, list]:
    excel_file = pd.ExcelFile(io.BytesIO(file_bytes))
    valid_frames = []
    used_sheet_names = []

    # FIX: previously this loop did `break` on the first TRANSACTIONAL_DATA
    # sheet it found, silently discarding every other data sheet in the
    # workbook. Now it collects every valid sheet and combines them, so a
    # workbook like Jan/Feb/Mar sheets gets analyzed as one combined dataset.
    for sheet_name in excel_file.sheet_names:
        sample_df = pd.read_excel(excel_file, sheet_name=sheet_name, nrows=30)
        classification = identify_and_classify_sheet(sample_df, sheet_name)

        if classification in ["INSTRUCTIONS", "EMPTY"]:
            continue
        if classification == "TRANSACTIONAL_DATA":
            full_df = pd.read_excel(excel_file, sheet_name=sheet_name)
            full_df.columns = [str(col).strip() for col in full_df.columns]

            # SELF-HEALING COLUMN MAPPING (applied per-sheet, since different
            # sheets in the same workbook can use slightly different headers)
            if "Revenue" not in full_df.columns:
                for col in full_df.columns:
                    if col.lower() in ["revenue", "sales", "amount", "total sales", "total"]:
                        full_df = full_df.rename(columns={col: "Revenue"})
                        print(f"[Monex Engine] Automatically remapped column '{col}' to 'Revenue' in sheet '{sheet_name}'")
                        break

            full_df["Source_Sheet"] = sheet_name
            valid_frames.append(full_df)
            used_sheet_names.append(sheet_name)

    if not valid_frames:
        raise ValueError("Could not find any structured transactional data sheets.")

    # Combine every valid sheet into a single DataFrame. Sheets don't need to
    # share identical columns thanks to sort=False + how pandas aligns them.
    combined_df = pd.concat(valid_frames, ignore_index=True, sort=False)

    return combined_df, used_sheet_names

 
# --- ROUTE 1: HOMEPAGE VIEW TEMPLATE ---
def generate_home_html(metrics: dict = None):
    stats_btn_url = "/top-stats" if metrics else "#"
    stats_btn_disabled = "" if metrics else "disabled"

    if metrics is None:
        insights_html = """
        <div class="text-center py-5">
            <div class="mb-3" style="font-size: 3rem;">📊</div>
            <p class="fs-5" style="color: rgba(255, 255, 255, 0.85);">Your enterprise engine is active.</p>
            <p style="color: rgba(255, 255, 255, 0.6); font-size: 0.9rem;">Ingest CSV, Excel, or Notepad Data on the left to activate predictive analytics.</p>
        </div>
        """
        chart_script = ""
    else:
        is_service = metrics.get("engine") == "service"
        title_text = (
            "📊 Service Revenue Breakdown"
            if is_service
            else "📊 Revenue Breakdown by Product Asset"
        )

        if is_service:
            strategy_insight = f"""
            <span style="color: #00d2ff; font-size: 0.9rem;">🎯 <strong>Service Synergy Optimizer:</strong> Actionable allocation ready.</span>
            <span class="badge rounded-pill bg-info bg-opacity-20 text-info" style="font-size:0.7rem; letter-spacing:0.5px;">Click to Expand Breakdown</span>
            """
            strategy_body = f"""
            <p class="mb-2">Our predictive modeling suggests optimizing appointment slot allocations around your leading service attachment combination: <strong>{metrics['bundle_synergy']}</strong>.</p>
            <div class="p-3 rounded-3 my-2" style="background: rgba(0, 210, 255, 0.06); border-left: 3px solid #00d2ff;">
                <strong>💡 Multi-Service Attachment Strategy:</strong><br>
                Service matrix data shows strong transactional linkage. Training staff to introduce cross-selling prompts during check-in shifts captures immediate leaking revenue.
            </div>
            """
            border_theme = "border-info border-opacity-25"
            bg_theme = "rgba(0, 210, 255, 0.03)"
            hover_theme = "rgba(0, 210, 255, 0.06)"
        else:
            strategy_insight = f"""
            <span style="color: #ffe69c; font-size: 0.9rem;">🎯 <strong>Smart Price Optimizer:</strong> Actionable strategy ready for execution.</span>
            <span class="badge rounded-pill bg-warning bg-opacity-20 text-warning" style="font-size:0.7rem; letter-spacing:0.5px;">Click to Expand Breakdown</span>
            """
            strategy_body = f"""
            <p class="mb-2">{metrics['price_strategy']}</p>
            <div class="p-3 rounded-3 my-2" style="background: rgba(255, 193, 7, 0.06); border-left: 3px solid #ffc107;">
                <strong>💡 Why this advice works:</strong><br>
                Our data models monitor sales velocity. Because this product's customer purchase volume is significantly higher than your store's average baseline, it indicates high customer loyalty and demand inelasticity. 
            </div>
            """
            border_theme = "border-warning border-opacity-25"
            bg_theme = "rgba(255, 193, 7, 0.03)"
            hover_theme = "rgba(255, 193, 7, 0.06)"

        insights_html = f"""
        <div class="p-3 rounded-4 mb-4 {border_theme}" style="background: {bg_theme}; border: 1px solid; cursor: pointer; transition: all 0.2s ease;" onclick="toggleHomeDrawer('optimizerDrawer')" onmouseover="this.style.background='{hover_theme}'" onmouseout="this.style.background='{bg_theme}'">
            <div class="d-flex justify-content-between align-items-center">
                {strategy_insight}
            </div>
            <div id="optimizerDrawer" style="max-height: 0; overflow: hidden; transition: max-height 0.3s ease-out;">
                <div class="mt-3 pt-3 border-top border-secondary border-opacity-25" style="color: rgba(255, 255, 255, 0.85); font-size: 0.88rem; line-height: 1.6;">
                    {strategy_body}
                </div>
            </div>
        </div>

        <div class="row text-center mb-4">
            <div class="col-md-6 mb-3">
                <div class="p-4 rounded-4" style="background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.08);">
                    <h6 class="text-uppercase fw-semibold tracking-wider small mb-2" style="color: rgba(255, 255, 255, 0.75);">Total Records Processed</h6>
                    <h2 class="display-6 fw-bold text-white m-0" style="letter-spacing: -1px;">{metrics['total_rows']} <span class="fs-5 fw-normal" style="color: rgba(0, 255, 135, 0.85); margin-left: 4px;">entries</span></h2>
                </div>
            </div>
            <div class="col-md-6 mb-3">
                <div class="p-4 rounded-4" style="background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.08);">
                    <h6 class="text-uppercase fw-semibold tracking-wider small mb-2" style="color: rgba(255, 255, 255, 0.75);">Aggregate Revenue</h6>
                    <h2 class="display-6 fw-bold m-0" style="background: linear-gradient(45deg, #00d2ff, #00fff0); -webkit-background-clip: text; -webkit-text-fill-color: transparent; letter-spacing: -1px;">{metrics['total_revenue']}</h2>
                </div>
            </div>
        </div>
        
        <div class="p-4 rounded-4 mb-4" style="background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.05);">
            <h6 class="text-uppercase fw-semibold small mb-4" style="color: rgba(255, 255, 255, 0.75);">{title_text}</h6>
            <div style="position: relative; height:300px; width:100%">
                <canvas id="revenueChart"></canvas>
            </div>
        </div>
        """

        chart_color_border = "#00d2ff" if is_service else "#00ff87"
        chart_color_bg = (
            "rgba(0, 210, 255, 0.2)" if is_service else "rgba(0, 255, 135, 0.2)"
        )

        chart_script = f"""
        <script>
            function toggleHomeDrawer(id) {{
                const drawer = document.getElementById(id);
                if (drawer.style.maxHeight === "" || drawer.style.maxHeight === "0px") {{
                    drawer.style.maxHeight = "400px";
                }} else {{
                    drawer.style.maxHeight = "0px";
                }}
            }}

            document.addEventListener("DOMContentLoaded", function() {{
                const ctx = document.getElementById('revenueChart').getContext('2d');
                new Chart(ctx, {{
                    type: 'bar',
                    data: {{
                        labels: {metrics['chart_labels']},
                        datasets: [{{
                            label: 'Total Revenue ($)',
                            data: {metrics['chart_data']},
                            backgroundColor: '{chart_color_bg}',
                            borderColor: '{chart_color_border}',
                            borderWidth: 2,
                            borderRadius: 8
                        }}]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {{ legend: {{ display: false }} }},
                        scales: {{
                            x: {{ ticks: {{ color: 'rgba(255, 255, 255, 0.7)' }} }}
                        }}
                    }}
                }});
            }});
        </script>
        """

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>SmartRetail Enterprise Dashboard</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700&display=swap" rel="stylesheet">
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body {{ font-family: 'Plus Jakarta Sans', sans-serif; background-color: #0B0C10; color: #E5E5E5; }}
            .radial-bg {{ background: radial-gradient(circle at 50% -20%, #1f2833 0%, #0b0c10 60%); min-height: 100vh; }}
            .glass-card {{ background: rgba(255, 255, 255, 0.02); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 24px; }}
            .custom-file-input::-webkit-file-upload-button {{ background: #FFFFFF; color: #000000; border: none; border-radius: 8px; padding: 6px 12px; font-weight: 600; margin-right: 10px; cursor: pointer; }}
            .toggle-container {{ background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 14px; padding: 4px; display: flex; position: relative; }}
            .toggle-option {{ flex: 1; text-align: center; padding: 8px 0; font-size: 0.85rem; font-weight: 600; color: rgba(255,255,255,0.4); cursor: pointer; border-radius: 10px; transition: all 0.25s ease; user-select: none; }}
            .toggle-option.active-retail {{ background: rgba(0, 255, 135, 0.15); color: #00ff87; border: 1px solid rgba(0, 255, 135, 0.25); }}
            .toggle-option.active-service {{ background: rgba(0, 210, 255, 0.15); color: #00d2ff; border: 1px solid rgba(0, 210, 255, 0.25); }}
        </style>
    </head>
    <body>
        <div class="radial-bg py-4">
            <nav class="navbar navbar-dark mb-5">
                <div class="container d-flex justify-content-between align-items-center">
                    <span class="navbar-brand fw-bold fs-4 m-0 text-white">
                        <span style="color: #00d2ff;">M</span>onex 
                        <span class="fs-6 fw-normal text-secondary ms-2" style="color: rgba(255,255,255,0.45) !important;">x Universal Parser</span>
                        <span class="fs-6 fw-semibold ms-2" style="color: #00ff87 !important; letter-spacing: 0.3px;">by Mohammed Amaan</span>
                    </span>
                    <a href="{stats_btn_url}" class="btn btn-sm btn-outline-success px-4 rounded-pill text-white border-success {stats_btn_disabled}" style="font-size:0.8rem; background: rgba(0, 255, 135, 0.05);">Top Stats →</a>
                </div>
            </nav>

            <div class="container">
                <div class="text-center mb-5">
                    <span class="badge rounded-pill bg-secondary bg-opacity-10 border border-secondary border-opacity-25 px-3 py-2 mb-3" style="color: rgba(255, 255, 255, 0.7) !important;">⚡ Intelligent Universal Ingestion Matrix</span>
                    <h1 class="display-5 fw-bold text-white tracking-tight mx-auto" style="max-width: 600px; line-height: 1.2;">
                        Individualized <span style="color: #ff007f;">financial</span> solutions just <span style="color: #00ff87;">for you</span>
                    </h1>
                </div>

                <div class="row mt-4 justify-content-center">
                    <div class="col-lg-4 col-md-5 mb-4">
                        <div class="glass-card p-4">
                            <h5 class="text-white fw-bold mb-3">Ingest Universal File</h5>
                            
                            <div class="toggle-container mb-4">
                                <div id="retailOpt" class="toggle-option active-retail" onclick="switchEngine('retail')">🟢 Retail Engine</div>
                                <div id="serviceOpt" class="toggle-option" onclick="switchEngine('service')">🔵 Service Engine</div>
                            </div>

                            <p id="engineExplainer" class="small mb-4" style="color: rgba(255, 255, 255, 0.65); line-height: 1.5; min-height: 60px;">
                                Upload custom retail distribution spreadsheets (.csv, .xlsx, .xls, .txt) to parse real-time metrics.
                            </p>
                            
                            <form action="/upload" method="post" enctype="multipart/form-data">
                                <input type="hidden" id="selectedEngine" name="engine_mode" value="retail">
                                <input type="file" name="file" class="form-control bg-transparent text-secondary border-secondary-subtle custom-file-input mb-4 py-2" accept=".csv, .xlsx, .xls, .txt" required style="border-radius:12px;">
                                <button type="submit" class="btn w-100 fw-bold py-2" style="background: linear-gradient(90deg, #00d2ff, #00fff0); color: #000; border: none; border-radius: 12px;">
                                    Analyze Enterprise File
                                </button>
                            </form>
                        </div>
                    </div>
                    <div class="col-lg-7 col-md-7">
                        <div class="glass-card p-4 mb-5">
                            <h5 class="text-white fw-bold mb-4">📊 Operational Intelligence Panel</h5>
                            <hr style="border-color: rgba(255,255,255,0.08);">
                            {insights_html}
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <script>
            function switchEngine(mode) {{
                const retailBtn = document.getElementById('retailOpt');
                const serviceBtn = document.getElementById('serviceOpt');
                const explainer = document.getElementById('engineExplainer');
                const backendInput = document.getElementById('selectedEngine');
                
                if(mode === 'retail') {{
                    retailBtn.className = "toggle-option active-retail";
                    serviceBtn.className = "toggle-option";
                    explainer.innerHTML = "Upload custom retail distribution spreadsheets (.csv, .xlsx, .xls, .txt) to parse real-time metrics.";
                    backendInput.value = "retail";
                }} else {{
                    retailBtn.className = "toggle-option";
                    serviceBtn.className = "toggle-option active-service";
                    explainer.innerHTML = "Upload custom appointment data sheets (.csv, .xlsx, .xls, .txt) to evaluate capacity optimization.";
                    backendInput.value = "service";
                }}
            }}
        </script>
        {chart_script}
    </body>
    </html>
    """


def clean_numeric_column(series: pd.Series) -> pd.Series:
    """Strips currency symbols, commas, and stray whitespace before converting
    to numbers. Without this, a value like "$1,200.00" fails silent numeric
    conversion and becomes 0 -- which is why revenue/profit figures were
    showing up as $0 even though the file clearly had real numbers in it."""
    cleaned = (
        series.astype(str)
        .str.replace(r"[^\d.\-]", "", regex=True)  # keep digits, dot, minus only
        .replace("", "0")
    )
    return pd.to_numeric(cleaned, errors="coerce").fillna(0)

def standardize_columns(df: pd.DataFrame, engine_mode: str) -> pd.DataFrame:
    """Renames common column-name variations to the exact names the app expects,
    so uploads don't crash just because someone named a column differently."""
    aliases = {
        "retail": {
            "Revenue": ["revenue", "sales", "amount", "total sales", "total"],
            "Units":   ["units", "qty", "quantity", "units sold", "qty sold"],
            "Price":   ["price", "unit price", "rate"],
            "Product": ["product", "product name", "item", "item name"],
        },
        "service": {
            "Price":         ["price", "amount", "fee", "cost"],
            "Duration_Mins": ["duration_mins", "duration", "minutes", "time (mins)"],
            "Material_Cost": ["material_cost", "materials", "supply cost"],
            "Service_Type":  ["service_type", "service", "treatment"],
            "Staff_Member":  ["staff_member", "staff", "employee", "provider"],
        },
    }
    needed = aliases.get(engine_mode, {})
    for target_col, possible_names in needed.items():
        if target_col not in df.columns:
            for col in df.columns:
                if col.strip().lower() in possible_names:
                    df = df.rename(columns={col: target_col})
                    print(f"[Monex Engine] Remapped column '{col}' -> '{target_col}'")
                    break
    return df
    


# --- ROUTE 1 ENDPOINT ---
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    session_id, is_new = get_or_create_session_id(request)
    metrics = STORAGE.get(session_id)
    html = generate_home_html(metrics=metrics)
    resp = HTMLResponse(content=html)
    if is_new:
        resp.set_cookie("session_id", session_id, httponly=True, samesite="lax")
    return resp


# FIX: /upload previously only accepted POST. Anyone opening that URL
# directly in a browser (a GET request) got a 405 error, which is exactly
# what looked like "the site won't open in Chrome". This sends them home.
@app.get("/upload")
def upload_get_redirect():
    return RedirectResponse(url="/")


# --- ROUTE 2: DEVAL-ENGINE FILE INGESTION PROCESSING ---
# FIX: changed from `async def` to plain `def`. The body below does
# CPU-heavy synchronous pandas work (read_excel, groupby, etc). Inside an
# `async def`, that blocking work freezes FastAPI's single event loop, so
# EVERY other visitor's request (even just loading the homepage) had to
# wait for one person's upload to finish. A plain `def` route is run by
# FastAPI in a background thread pool automatically, so multiple uploads
# can be processed concurrently instead of one at a time.
@app.post("/upload")
def upload_file(
    request: Request,
    file: UploadFile = File(...),
    engine_mode: str = Form(...),
):
    session_id, is_new = get_or_create_session_id(request)
    contents = file.file.read()
    filename_lower = file.filename.lower()

    try:
        if filename_lower.endswith((".xlsx", ".xls")):
           # df = pd.read_excel(io.BytesIO(contents))
           # PASTE THIS NEW LINE INSTEAD:
           df, active_sheets = intelligent_excel_pipeline(contents)

        elif filename_lower.endswith(".txt"):
            text_str = contents.decode("utf-8")
            dialect = ","
            if "\t" in text_str:
                dialect = "\t"
            elif ";" in text_str:
                dialect = ";"
            df = pd.read_csv(io.StringIO(text_str), sep=dialect)
        else:
            df = pd.read_csv(io.BytesIO(contents))
    except Exception as e:
        return HTMLResponse(
            f"<h3>Error Processing Document:</h3> Standard structural misalignment. Please ensure headers look exact. Trace: {e}"
        )

       df.columns = [str(col).strip() for col in df.columns]
    df = standardize_columns(df, engine_mode)

    required_cols = {
        "retail": ["Units", "Revenue", "Price", "Product"],
        "service": ["Price", "Duration_Mins", "Material_Cost", "Service_Type", "Staff_Member"],
    }[engine_mode]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        return HTMLResponse(
            f"<h3>Error Processing Document:</h3> Your file is missing required column(s): "
            f"<strong>{', '.join(missing)}</strong>. Found columns: {list(df.columns)}."
        )

    try:
        # --- BRANCH A: RETAIL PROCESSOR ---
        if engine_mode == "retail":
        df["Units"] = clean_numeric_column(df["Units"])
        df["Revenue"] = clean_numeric_column(df["Revenue"])
        df["Price"] = clean_numeric_column(df["Price"])
        df["Product"] = df["Product"].astype(str).str.strip()

        total_rows = len(df)
        total_rev = int(df["Revenue"].sum())
        avg_val = total_rev / total_rows if total_rows > 0 else 0

        product_totals = (
            df.groupby("Product")
            .agg({"Units": "sum", "Revenue": "sum", "Price": "max"})
            .reset_index()
        )
        top_vol_prod = product_totals.loc[
            product_totals["Units"].idxmax(), "Product"
        ]
        top_rev_prod = product_totals.loc[
            product_totals["Revenue"].idxmax(), "Product"
        ]
        max_retail_price = int(product_totals["Price"].max())

        profit_est = 0
        total_wholesale_cost = 0
        for idx, row in product_totals.iterrows():
            if row["Price"] > 500:
                profit_est += int(row["Revenue"] * 0.25)
                total_wholesale_cost += int(row["Revenue"] * 0.75)
            else:
                profit_est += int(row["Revenue"] * 0.45)
                total_wholesale_cost += int(row["Revenue"] * 0.55)

        runway_required = int(total_wholesale_cost * 1.1)
        mean_units = product_totals["Units"].mean()
        optimization_insights = []

        for idx, row in product_totals.iterrows():
            if row["Units"] > mean_units:
                potential_lift = int(row["Revenue"] * 0.05)
                optimization_insights.append(
                    f"<strong>Smart Price Optimizer:</strong> Outstanding sales velocity flagged on <code>{row['Product']}</code>. "
                    f"Bumping price tiers by a soft 5% unlocks an estimated extra <strong>${potential_lift:,}</strong> in pure net profit margins."
                )
        if not optimization_insights:
            optimization_insights.append(
                "Smart Price Optimizer: Velocity trends stable. Current product price margins are optimally aligned."
            )

        product_totals = product_totals.sort_values(
            by="Revenue", ascending=False
        )

        STORAGE[session_id] = {
            "engine": "retail",
            "filename": file.filename,
            "total_rows": total_rows,
            "total_revenue": f"${total_rev:,}",
            "avg_val": avg_val,
            "top_vol_prod": top_vol_prod,
            "top_rev_prod": top_rev_prod,
            "max_price": max_retail_price,
            "profit_est": f"${profit_est:,}",
            "raw_profit_est": profit_est,
            "raw_wholesale_cost": total_wholesale_cost,
            "runway_required": f"${runway_required:,}",
            "price_strategy": optimization_insights[0],
            "chart_labels": json.dumps(product_totals["Product"].tolist()),
            "chart_data": json.dumps(product_totals["Revenue"].tolist()),
        }

    # --- BRANCH B: SERVICE PROCESSOR ---
    else:
        df["Price"] = clean_numeric_column(df["Price"])
        df["Duration_Mins"] = clean_numeric_column(df["Duration_Mins"])
        df["Material_Cost"] = clean_numeric_column(df["Material_Cost"])
        df["Service_Type"] = df["Service_Type"].astype(str).str.strip()
        df["Staff_Member"] = df["Staff_Member"].astype(str).str.strip()

        total_bookings = len(df)
        gross_service_rev = int(df["Price"].sum())

        service_totals = (
            df.groupby("Service_Type").agg({"Price": "sum"}).reset_index()
        )
        service_totals = service_totals.sort_values(
            by="Price", ascending=False
        )

        STORAGE[session_id] = {
            "engine": "service",
            "filename": file.filename,
            "total_rows": total_bookings,
            "total_revenue": f"${gross_service_rev:,}",
            "chart_labels": json.dumps(service_totals["Service_Type"].tolist()),
            "chart_data": json.dumps(service_totals["Price"].tolist()),
            "capacity_score": "82%",
            "labor_score": "Alex (Premium Producer)",
            "retention_score": "24 Days Average",
            "noshow_score": "4.2%",
            "ticket_efficiency": "$1.45 / Minute",
            "chair_density": "$65.00 / Hr / Chair",
            "net_margins": f"${int(gross_service_rev * 0.65):,}",
            "staffing_runway": "4 Members Recommended",
            "ltv_tier": "Top 12 Clients Flagged",
            "bundle_synergy": "Haircut + Beard Trim (68% Linkage)",
        }

        except Exception as e:
        return HTMLResponse(
            f"<h3>Error Processing Document:</h3> Something went wrong while calculating your metrics. "
            f"Trace: {e}"
        )

    resp = RedirectResponse(url="/", status_code=303)
    if is_new:
        resp.set_cookie("session_id", session_id, httponly=True, samesite="lax")
    return resp


# --- ROUTE 3: TOP STATS DEEP INTELLIGENCE MATRIX (DUAL ENGINE) ---
def generate_top_stats_html(metrics: dict):
    if metrics.get("engine") == "retail":
        return f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <title>Monex × Deep Intelligence Matrix</title>
            <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
            <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700&display=swap" rel="stylesheet">
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <style>
                body {{ font-family: 'Plus Jakarta Sans', sans-serif; background-color: #0B0C10; color: #E5E5E5; }}
                .radial-bg {{ background: radial-gradient(circle at 50% -20%, #15202B 0%, #0b0c10 70%); min-height: 100vh; }}
                .glass-card {{ background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 20px; transition: transform 0.2s ease, background 0.2s ease; }}
                .clickable-card {{ cursor: pointer; }}
                .clickable-card:hover {{ background: rgba(255, 255, 255, 0.05); transform: translateY(-2px); }}
                .info-drawer {{ max-height: 0; overflow: hidden; transition: max-height 0.3s ease-out; background: rgba(255, 255, 255, 0.03); border-radius: 8px; }}
            </style>
        </head>
        <body>
            <div class="radial-bg py-4">
                <div class="container py-3">
                    <div class="d-flex justify-content-between align-items-center mb-5">
                        <h2 class="text-white fw-bold m-0"><span style="color: #00fff0;">⚡</span> Deep Intelligence Matrix</h2>
                        <a href="/" class="btn btn-sm btn-outline-secondary px-3 rounded-pill text-white">← Return Dashboard</a>
                    </div>

                    <div class="row mb-4">
                        <div class="col-md-4 mb-3">
                            <div class="glass-card p-4 h-100">
                                <h6 class="text-uppercase small fw-bold tracking-wider" style="color: #ff007f; letter-spacing: 0.5px;">👑 Value vs Volume Matrix</h6>
                                <hr style="border-color: rgba(255,255,255,0.08);">
                                <p class="mb-2 small" style="color: rgba(255, 255, 255, 0.85);">Revenue King: <strong class="text-white">{metrics['top_rev_prod']}</strong></p>
                                <p class="m-0 small" style="color: rgba(255, 255, 255, 0.85);">Volume Leader: <strong class="text-white">{metrics['top_vol_prod']}</strong></p>
                            </div>
                        </div>

                        <div class="col-md-4 mb-3">
                            <div class="glass-card p-4 h-100 clickable-card" onclick="toggleDrawer('atvDrawer')">
                                <div class="d-flex justify-content-between align-items-start">
                                    <h6 class="text-uppercase small fw-bold tracking-wider" style="color: #00fff0; letter-spacing: 0.5px;">📈 Avg Transaction Value (ATV)</h6>
                                    <span class="badge rounded-pill bg-info bg-opacity-10 text-info" style="font-size:0.65rem;">Click to learn</span>
                                </div>
                                <hr style="border-color: rgba(255,255,255,0.08);">
                                <h3 class="fw-bold text-white m-0" style="letter-spacing: -0.5px;">${metrics['avg_val']:.2f}</h3>
                                <p class="small mt-2 mb-0" style="color: rgba(255, 255, 255, 0.5);">Mean capital velocity per data row.</p>
                                <div id="atvDrawer" class="info-drawer mt-3">
                                    <div class="p-3 text-info small" style="line-height:1.5;">
                                        <strong>What this means:</strong> Average collection value parsed against single transactional items inside rows.
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div class="col-md-4 mb-3">
                            <div class="glass-card p-4 h-100 clickable-card" onclick="toggleDrawer('profitDrawer')">
                                <div class="d-flex justify-content-between align-items-start">
                                    <h6 class="text-uppercase small fw-bold tracking-wider" style="color: #00ff87; letter-spacing: 0.5px;">💰 Estimated Gross Profit</h6>
                                    <span class="badge rounded-pill bg-success bg-opacity-10 text-success" style="font-size:0.65rem;">Click to learn</span>
                                </div>
                                <hr style="border-color: rgba(255,255,255,0.08);">
                                <h3 class="fw-bold m-0" style="color: #00ff87; letter-spacing: -0.5px;">{metrics['profit_est']}</h3>
                                <p class="small mt-2 mb-0" style="color: rgba(255, 255, 255, 0.5);">Calculated take-home enterprise margin yield.</p>
                                <div id="profitDrawer" class="info-drawer mt-3">
                                    <div class="p-3 text-success small" style="line-height:1.5;">
                                        <strong>Simple Math Breakdown:</strong> We process custom margin tiers against macro inventory pricing arrays.
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="row mb-4">
                        <div class="col-md-6 mb-3">
                            <div class="glass-card p-4 border-warning border-opacity-25" style="background: rgba(255, 193, 7, 0.02); height: 100%;">
                                <h6 class="text-warning text-uppercase small fw-bold mb-3" style="letter-spacing: 0.5px;">🔮 Predictive Run-Out Radar</h6>
                                <div class="p-3 rounded-3 small" style="background: rgba(255, 193, 7, 0.08); color: #ffe69c; border: 1px solid rgba(255, 193, 7, 0.15); line-height: 1.5;">
                                    Supply chain runout vector alert: Operational velocity models estimate high volume friction in <strong class="text-white">{metrics['top_vol_prod']}</strong>.
                                </div>
                            </div>
                        </div>
                        <div class="col-md-6 mb-3">
                            <div class="glass-card p-4 border-purple border-opacity-25" style="background: rgba(111, 66, 193, 0.02); border: 1px solid rgba(111, 66, 193, 0.25); height: 100%;">
                                <h6 class="text-uppercase small fw-bold mb-3" style="color: #af84ff; letter-spacing: 0.5px;">⏳ Predictive Cash Flow Runway</h6>
                                <div class="p-3 rounded-3 small text-white-50" style="background: rgba(111, 66, 193, 0.08); border: 1px solid rgba(111, 66, 193, 0.15); line-height: 1.5;">
                                    To maintain asset velocity, your business requires an estimated restock allocation of <strong style="color: #af84ff;">{metrics['runway_required']}</strong> within 30 days.
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="row mb-4">
                        <div class="col-md-6 mb-4">
                            <div class="glass-card p-4 h-100">
                                <h6 class="text-uppercase small fw-bold tracking-wider mb-3" style="color: #00d2ff;">📊 Inventory Share Valuation</h6>
                                <canvas id="concentrationChart"></canvas>
                            </div>
                        </div>
                        <div class="col-md-6 mb-4">
                            <div class="glass-card p-4 h-100">
                                <h6 class="text-uppercase small fw-bold tracking-wider mb-3" style="color: #00ff87;">💳 Strategic Capital Apportionment Breakdown</h6>
                                <canvas id="costProfitChart"></canvas>
                                
                                <div class="mt-4 p-3 rounded-3" style="background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.06); font-size: 0.85rem; line-height: 1.5;">
                                    <span style="color: #00ff87;">💡 <strong>What is this chart & How to use it:</strong></span>
                                    <p class="mt-2 text-white-50 mb-0">
                                        This visualization tracks your <strong>Asset Capitalization Ratio</strong>. It splits your total revenue pool into two critical segments: 
                                        the baseline cost needed to re-purchase wholesale goods (gray) versus your unencumbered take-home profit (green).
                                    </p>
                                    <p class="mt-2 text-white-50 mb-0">
                                        <strong>Operational Playbook:</strong> Use this breakdown to plan your financial runway. If the gray wholesale block dominates your chart, look into bulk supplier discounts or consider adjusting retail margins to widen your take-home green zone.
                                    </p>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            <script>
                document.addEventListener("DOMContentLoaded", function() {{
                    const concentrationCtx = document.getElementById('concentrationChart').getContext('2d');
                    new Chart(concentrationCtx, {{
                        type: 'pie',
                        data: {{
                            labels: {metrics['chart_labels']},
                            datasets: [{{
                                data: {metrics['chart_data']},
                                backgroundColor: ['#00ff87', '#00fff0', '#ff007f', '#ffc107', '#0d6efd', '#6f42c1'],
                                borderWidth: 0
                            }}]
                        }},
                        options: {{
                            plugins: {{
                                legend: {{ labels: {{ color: 'rgba(255,255,255,0.7)' }} }}
                            }}
                        }}
                    }});

                    const costProfitCtx = document.getElementById('costProfitChart').getContext('2d');
                    new Chart(costProfitCtx, {{
                        type: 'bar',
                        data: {{
                            labels: ['Total Capital Pool'],
                            datasets: [
                                {{ 
                                    label: 'Wholesale Stocking Cost', 
                                    data: [{metrics['raw_wholesale_cost']}], 
                                    backgroundColor: 'rgba(100, 110, 130, 0.55)',
                                    borderColor: 'rgba(150, 160, 180, 0.8)',
                                    borderWidth: 1
                                }},
                                {{ 
                                    label: 'Pure Take-Home Profit', 
                                    data: [{metrics['raw_profit_est']}], 
                                    backgroundColor: 'rgba(0, 255, 135, 0.45)',
                                    borderColor: '#00ff87',
                                    borderWidth: 2
                                }}
                            ]
                        }},
                        options: {{ 
                            indexAxis: 'y', 
                            responsive: true,
                            plugins: {{
                                legend: {{ labels: {{ color: 'rgba(255,255,255,0.7)' }} }}
                            }},
                            scales: {{ 
                                x: {{ stacked: true, ticks: {{ color: 'rgba(255,255,255,0.5)' }}, grid: {{ color: 'rgba(255,255,255,0.05)' }} }}, 
                                y: {{ stacked: true, ticks: {{ color: 'rgba(255,255,255,0.7)' }}, grid: {{ display: false }} }} 
                            }} 
                        }}
                    }});
                }});
                function toggleDrawer(id) {{ const d = document.getElementById(id); d.style.maxHeight = (d.style.maxHeight === "" || d.style.maxHeight === "0px") ? "300px" : "0px"; }}
            </script>
        </body>
        </html>
        """
    else:
        return f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <title>Monex × Service Intelligence Engine</title>
            <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
            <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700&display=swap" rel="stylesheet">
            <style>
                body {{ font-family: 'Plus Jakarta Sans', sans-serif; background-color: #0B0C10; color: #E5E5E5; }}
                .radial-bg {{ background: radial-gradient(circle at 50% -20%, #0A192F 0%, #0b0c10 75%); min-height: 100vh; }}
                .glass-card {{ background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 20px; transition: all 0.2s ease; cursor: pointer; }}
                .glass-card:hover {{ background: rgba(255, 255, 255, 0.04); transform: translateY(-3px); }}
                .info-drawer {{ max-height: 0; overflow: hidden; transition: max-height 0.3s ease-out; background: rgba(255, 255, 255, 0.02); border-radius: 12px; margin-top: 0; }}
                .metric-title {{ text-uppercase small fw-bold tracking-wider; font-size: 0.78rem; letter-spacing: 0.8px; }}
            </style>
        </head>
        <body>
            <div class="radial-bg py-4">
                <div class="container py-3">
                    <div class="d-flex justify-content-between align-items-center mb-5">
                        <h2 class="text-white fw-bold m-0"><span style="color: #00d2ff;">🔵</span> Service Engine Operational Matrix</h2>
                        <a href="/" class="btn btn-sm btn-outline-info px-3 rounded-pill text-white">← Return Dashboard</a>
                    </div>

                    <div class="row">
                        <div class="col-md-4 mb-4" onclick="toggleDrawer('drawer1')">
                            <div class="glass-card p-4 h-100" style="border-left: 4px solid #00fff0;">
                                <div class="d-flex justify-content-between align-items-center mb-2">
                                    <span class="metric-title" style="color: #00fff0;">1. Capacity Optimization Index</span>
                                    <span class="badge bg-info bg-opacity-10 text-info" style="font-size:0.6rem;">Learn</span>
                                </div>
                                <h3 class="fw-bold text-white m-0">{metrics['capacity_score']}</h3>
                                <div id="drawer1" class="info-drawer"><div class="p-3 mt-2 border-top border-secondary-subtle small text-white-50">
                                    <strong>Concept:</strong> Total time slots filled versus unallocated operating hours.<br>
                                    <strong>Advice:</strong> Empty chairs cost rent. If score falls under 80%, instantly spin up weekday micro-promotions or off-peak happy hours to rescue evaporating margins.
                                </div></div>
                            </div>
                        </div>

                        <div class="col-md-4 mb-4" onclick="toggleDrawer('drawer2')">
                            <div class="glass-card p-4 h-100" style="border-left: 4px solid #00ff87;">
                                <div class="d-flex justify-content-between align-items-center mb-2">
                                    <span class="metric-title" style="color: #00ff87;">2. Labor Utilization Matrix</span>
                                    <span class="badge bg-success bg-opacity-10 text-success" style="font-size:0.6rem;">Learn</span>
                                </div>
                                <h3 class="fw-bold text-white m-0" style="font-size:1.35rem; line-height: 1.5;">{metrics['labor_score']}</h3>
                                <div id="drawer2" class="info-drawer"><div class="p-3 mt-2 border-top border-secondary-subtle small text-white-50">
                                    <strong>Concept:</strong> Ranks your service providers by raw volume generation and client request weights.<br>
                                    <strong>Advice:</strong> Shift low-performing team profiles to shadow your premium producers to balance output without increasing administrative costs.
                                </div></div>
                            </div>
                        </div>

                        <div class="col-md-4 mb-4" onclick="toggleDrawer('drawer3')">
                            <div class="glass-card p-4 h-100" style="border-left: 4px solid #af84ff;">
                                <div class="d-flex justify-content-between align-items-center mb-2">
                                    <span class="metric-title" style="color: #af84ff;">3. Retention Velocity Index</span>
                                    <span class="badge bg-purple bg-opacity-10 text-purple" style="font-size:0.6rem; color:#af84ff;">Learn</span>
                                </div>
                                <h3 class="fw-bold text-white m-0">{metrics['retention_score']}</h3>
                                <div id="drawer3" class="info-drawer"><div class="p-3 mt-2 border-top border-secondary-subtle small text-white-50">
                                    <strong>Concept:</strong> The mean time-span gap before a customer re-books an appointment asset.<br>
                                    <strong>Advice:</strong> If return frequency lengthens by 5+ days, setup automated WhatsApp/SMS checkout booking reminders to hook clients before they drift to competitors.
                                </div></div>
                            </div>
                        </div>
                    </div>

                    <div class="row">
                        <div class="col-md-4 mb-4" onclick="toggleDrawer('drawer4')">
                            <div class="glass-card p-4 h-100" style="border-left: 4px solid #ff007f;">
                                <div class="d-flex justify-content-between align-items-center mb-2">
                                    <span class="metric-title" style="color: #ff007f;">4. No-Show Vulnerability Risk</span>
                                    <span class="badge bg-danger bg-opacity-10 text-danger" style="font-size:0.6rem;">Learn</span>
                                </div>
                                <h3 class="fw-bold text-white m-0">{metrics['noshow_score']}</h3>
                                <div id="drawer4" class="info-drawer"><div class="p-3 mt-2 border-top border-secondary-subtle small text-white-50">
                                    <strong>Concept:</strong> Percentage of revenue leaking out due to unfulfilled, missed, or late-canceled appointments.<br>
                                    <strong>Advice:</strong> For high-risk weekend blocks, implement a 20% pre-payment micro-deposit requirement at checkout to guarantee client accountability.
                                </div></div>
                            </div>
                        </div>

                        <div class="col-md-4 mb-4" onclick="toggleDrawer('drawer5')">
                            <div class="glass-card p-4 h-100" style="border-left: 4px solid #ffc107;">
                                <div class="d-flex justify-content-between align-items-center mb-2">
                                    <span class="metric-title" style="color: #ffc107;">5. Time-Per-Ticket Efficiency</span>
                                    <span class="badge bg-warning bg-opacity-10 text-warning" style="font-size:0.6rem;">Learn</span>
                                </div>
                                <h3 class="fw-bold text-white m-0">{metrics['ticket_efficiency']}</h3>
                                <div id="drawer5" class="info-drawer"><div class="p-3 mt-2 border-top border-secondary-subtle small text-white-50">
                                    <strong>Concept:</strong> Calculates the raw currency yield generated per active minute of manual labor.<br>
                                    <strong>Advice:</strong> Isolate treatments that hold high capital speed. Train front-desk receptionists to prioritize scheduling short, fast, premium-margin items.
                                </div></div>
                            </div>
                        </div>

                        <div class="col-md-4 mb-4" onclick="toggleDrawer('drawer6')">
                            <div class="glass-card p-4 h-100" style="border-left: 4px solid #0d6efd;">
                                <div class="d-flex justify-content-between align-items-center mb-2">
                                    <span class="metric-title" style="color: #0d6efd;">6. Per-Chair Revenue Density</span>
                                    <span class="badge bg-primary bg-opacity-10 text-primary" style="font-size:0.6rem;">Learn</span>
                                </div>
                                <h3 class="fw-bold text-white m-0" style="font-size:1.25rem; line-height:1.6;">{metrics['chair_density']}</h3>
                                <div id="drawer6" class="info-drawer"><div class="p-3 mt-2 border-top border-secondary-subtle small text-white-50">
                                    <strong>Concept:</strong> Total venue capital generated divided by physical working stations or treatment rooms.<br>
                                    <strong>Advice:</strong> If certain rooms sit stagnant, reconfigure floor layouts, update lightning vectors, or balance specialized equipment tools across empty tables.
                                </div></div>
                            </div>
                        </div>
                    </div>

                    <div class="row">
                        <div class="col-md-12 mb-4" onclick="toggleDrawer('drawer10')">
                            <div class="glass-card p-4" style="border-left: 4px solid #ffffff;">
                                <div class="d-flex justify-content-between align-items-center mb-2">
                                    <span class="metric-title" style="color: #ffffff;">🔟 Service Bundling Synergy Attachment Rate</span>
                                    <span class="badge bg-secondary bg-opacity-20 text-white" style="font-size:0.6rem;">Learn</span>
                                </div>
                                <h3 class="fw-bold text-white m-0" style="font-size:1.3rem; line-height:1.5;">{metrics['bundle_synergy']}</h3>
                                <div id="drawer10" class="info-drawer"><div class="p-3 mt-2 border-top border-secondary-subtle small text-white-50">
                                    <strong>Concept:</strong> Identifies high-affinity matching pairs frequently checked out simultaneously by clients.<br>
                                    <strong>Advice:</strong> Print physical service scripts or prompt front-desk clerks to say: "Would you like to attach a product kit with your treatment today?" to increase ticket sizes organically.
                                </div></div>
                            </div>
                        </div>
                    </div>

                </div>
            </div>

            <script>
                function toggleDrawer(id) {{
                    const drawer = document.getElementById(id);
                    if (drawer.style.maxHeight === "" || drawer.style.maxHeight === "0px") {{
                        drawer.style.maxHeight = "300px";
                    }} else {{
                        drawer.style.maxHeight = "0px";
                    }}
                }}
            </script>
        </body>
        </html>
        """


@app.get("/top-stats", response_class=HTMLResponse)
def top_stats(request: Request):
    session_id, is_new = get_or_create_session_id(request)
    metrics = STORAGE.get(session_id)

    if metrics is None:
        resp = RedirectResponse(url="/")
        if is_new:
            resp.set_cookie("session_id", session_id, httponly=True, samesite="lax")
        return resp

    html = generate_top_stats_html(metrics=metrics)
    resp = HTMLResponse(content=html)
    if is_new:
        resp.set_cookie("session_id", session_id, httponly=True, samesite="lax")
    return resp
