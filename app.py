import streamlit as st
import anthropic
import base64
import json
import math
import fitz
import ezdxf
from ezdxf.enums import TextEntityAlignment
from json_repair import repair_json

st.set_page_config(page_title="貝克裝修｜平面圖產生器", page_icon="📐", layout="wide")

# ==========================================
# PDF 轉圖片
# ==========================================
def pdf_to_images_b64(pdf_bytes, dpi=150):
    images_b64 = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    for page in doc:
        mat = fitz.Matrix(dpi/72, dpi/72)
        pix = page.get_pixmap(matrix=mat)
        images_b64.append(base64.b64encode(pix.tobytes("jpeg")).decode("utf-8"))
    doc.close()
    return images_b64

# ==========================================
# AI 讀取平面圖
# ==========================================
def extract_floor_plan(api_key, images_b64):
    client = anthropic.Anthropic(api_key=api_key)
    system = (
        "你是室內設計平面圖解讀專家。從手繪平面圖提取空間資訊。"
        "尺寸統一用公尺(m)，cm請換算。座標：左上角(0,0)，x向右，y向下。"
        "重要：外牆輪廓必須用多邊形頂點列表描述，不規則形狀請列出所有轉角座標。"
        "房間的x,y是左上角座標，w是寬，h是高。"
        "門的位置：x,y是門開口的起點座標，width是門寬，wall是所在牆（top/bottom/left/right），room是所屬房間名稱。"
        "窗戶同門格式。"
        "規則：只輸出純JSON，字串內不得有換行符。"
        'JSON格式：'
        '{"total_width_m":10.3,"total_height_m":9.0,'
        '"outer_polygon":[{"x":0,"y":0},{"x":10.3,"y":0},{"x":10.3,"y":9.0},{"x":0,"y":9.0}],'
        '"rooms":[{"name":"客廳","x":0,"y":0,"w":5.6,"h":4.0}],'
        '"doors":[{"x":2.5,"y":0,"width":0.9,"wall":"top","room":"客廳"}],'
        '"windows":[{"x":1.0,"y":0,"width":1.5,"wall":"top"}],'
        '"notes":"備註"}'
    )
    content = []
    for img_b64 in images_b64:
        content.append({"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":img_b64}})
    content.append({
        "type":"text",
        "text":(
            "請仔細分析這份手繪平面圖。\n"
            "1. 外牆是不規則形狀，請列出所有轉角頂點座標（outer_polygon）\n"
            "2. 每個房間/空間都要識別並標上名稱\n"
            "3. 所有門的位置和開啟方向\n"
            "4. 所有窗戶位置\n"
            "5. 尺寸從圖面上的紅色數字讀取，單位是公分，換算成公尺\n"
            "只輸出JSON。"
        )
    })
    response = client.messages.create(
        model="claude-opus-4-5", max_tokens=6000, system=system,
        messages=[{"role":"user","content":content}]
    )
    raw = response.content[0].text.strip()
    s, e = raw.find('{'), raw.rfind('}')
    if s != -1 and e != -1:
        raw = raw[s:e+1]
    return json.loads(repair_json(raw))

# ==========================================
# SVG 產生器（支援不規則外牆）
# ==========================================
def generate_svg(data, width_px=680):
    tw = data.get("total_width_m", 10)
    th = data.get("total_height_m", 8)
    rooms = data.get("rooms", [])
    doors = data.get("doors", [])
    windows = data.get("windows", [])
    outer = data.get("outer_polygon", [])

    MARGIN = 55
    scale = (width_px - MARGIN * 2) / tw
    height_px = int(th * scale) + MARGIN * 2 + 30
    FONT = "Arial, sans-serif"

    def mx(x): return MARGIN + x * scale
    def my(y): return MARGIN + y * scale
    def ms(v): return v * scale

    L = []
    L.append(f'<svg width="{width_px}" height="{height_px}" viewBox="0 0 {width_px} {height_px}" xmlns="http://www.w3.org/2000/svg">')
    L.append('<rect width="100%" height="100%" fill="white"/>')

    # 外牆（多邊形）
    if outer and len(outer) >= 3:
        pts = " ".join(f"{mx(p['x']):.1f},{my(p['y']):.1f}" for p in outer)
        L.append(f'<polygon points="{pts}" fill="white" stroke="black" stroke-width="6" stroke-linejoin="miter"/>')
    else:
        # fallback 矩形
        L.append(f'<rect x="{mx(0):.1f}" y="{my(0):.1f}" width="{ms(tw):.1f}" height="{ms(th):.1f}" fill="white" stroke="black" stroke-width="6"/>')

    # 內牆（房間邊框）
    for room in rooms:
        rx,ry,rw,rh = room.get("x",0),room.get("y",0),room.get("w",0),room.get("h",0)
        L.append(f'<rect x="{mx(rx):.1f}" y="{my(ry):.1f}" width="{ms(rw):.1f}" height="{ms(rh):.1f}" fill="none" stroke="black" stroke-width="3"/>')

    # 窗戶（雙平行線）
    GAP = 7
    for win in windows:
        wx,wy,ww = win.get("x",0),win.get("y",0),win.get("width",1.0)
        wall = win.get("wall","top")
        if wall in ("top","bottom"):
            yb = my(0) if wall=="top" else my(th)
            x1,x2 = mx(wx),mx(wx+ww)
            d = GAP if wall=="top" else -GAP
            L.append(f'<line x1="{x1:.1f}" y1="{yb:.1f}" x2="{x2:.1f}" y2="{yb:.1f}" stroke="white" stroke-width="8"/>')
            L.append(f'<line x1="{x1:.1f}" y1="{yb:.1f}" x2="{x2:.1f}" y2="{yb:.1f}" stroke="black" stroke-width="1.5"/>')
            L.append(f'<line x1="{x1:.1f}" y1="{yb+d:.1f}" x2="{x2:.1f}" y2="{yb+d:.1f}" stroke="black" stroke-width="1.5"/>')
            L.append(f'<line x1="{x1:.1f}" y1="{yb:.1f}" x2="{x1:.1f}" y2="{yb+d:.1f}" stroke="black" stroke-width="1.5"/>')
            L.append(f'<line x1="{x2:.1f}" y1="{yb:.1f}" x2="{x2:.1f}" y2="{yb+d:.1f}" stroke="black" stroke-width="1.5"/>')
        else:
            xb = mx(0) if wall=="left" else mx(tw)
            y1,y2 = my(wy),my(wy+ww)
            d = GAP if wall=="left" else -GAP
            L.append(f'<line x1="{xb:.1f}" y1="{y1:.1f}" x2="{xb:.1f}" y2="{y2:.1f}" stroke="white" stroke-width="8"/>')
            L.append(f'<line x1="{xb:.1f}" y1="{y1:.1f}" x2="{xb:.1f}" y2="{y2:.1f}" stroke="black" stroke-width="1.5"/>')
            L.append(f'<line x1="{xb+d:.1f}" y1="{y1:.1f}" x2="{xb+d:.1f}" y2="{y2:.1f}" stroke="black" stroke-width="1.5"/>')
            L.append(f'<line x1="{xb:.1f}" y1="{y1:.1f}" x2="{xb+d:.1f}" y2="{y1:.1f}" stroke="black" stroke-width="1.5"/>')
            L.append(f'<line x1="{xb:.1f}" y1="{y2:.1f}" x2="{xb+d:.1f}" y2="{y2:.1f}" stroke="black" stroke-width="1.5"/>')

    # 門（開口+弧線）
    for door in doors:
        dx,dy,dw = door.get("x",0),door.get("y",0),door.get("width",0.9)
        wall = door.get("wall","top")
        r = ms(dw)
        if wall in ("top","bottom"):
            yb = my(0) if wall=="top" else my(th)
            x1,x2 = mx(dx),mx(dx+dw)
            arc_y = yb+r if wall=="top" else yb-r
            sweep = "1" if wall=="top" else "0"
            L.append(f'<line x1="{x1:.1f}" y1="{yb:.1f}" x2="{x2:.1f}" y2="{yb:.1f}" stroke="white" stroke-width="8"/>')
            L.append(f'<line x1="{x1:.1f}" y1="{yb:.1f}" x2="{x1:.1f}" y2="{arc_y:.1f}" stroke="black" stroke-width="1.5"/>')
            L.append(f'<path d="M{x1:.1f},{yb:.1f} A{r:.1f},{r:.1f} 0 0 {sweep} {x2:.1f},{arc_y:.1f}" fill="none" stroke="black" stroke-width="1" stroke-dasharray="4,2"/>')
        else:
            xb = mx(0) if wall=="left" else mx(tw)
            y1d,y2d = my(dy),my(dy+dw)
            arc_x = xb+r if wall=="left" else xb-r
            sweep = "0" if wall=="left" else "1"
            L.append(f'<line x1="{xb:.1f}" y1="{y1d:.1f}" x2="{xb:.1f}" y2="{y2d:.1f}" stroke="white" stroke-width="8"/>')
            L.append(f'<line x1="{xb:.1f}" y1="{y1d:.1f}" x2="{arc_x:.1f}" y2="{y1d:.1f}" stroke="black" stroke-width="1.5"/>')
            L.append(f'<path d="M{xb:.1f},{y1d:.1f} A{r:.1f},{r:.1f} 0 0 {sweep} {arc_x:.1f},{y2d:.1f}" fill="none" stroke="black" stroke-width="1" stroke-dasharray="4,2"/>')

    # 房間標籤
    for room in rooms:
        rx,ry,rw,rh = room.get("x",0),room.get("y",0),room.get("w",0),room.get("h",0)
        cx,cy = mx(rx+rw/2), my(ry+rh/2)
        fs = max(9, min(14, int(ms(min(rw,rh))*0.18)))
        L.append(f'<text x="{cx:.1f}" y="{cy-4:.1f}" font-family="{FONT}" font-size="{fs}" fill="black" text-anchor="middle" font-weight="bold">{room.get("name","")}</text>')
        L.append(f'<text x="{cx:.1f}" y="{cy+fs:.1f}" font-family="{FONT}" font-size="{max(8,fs-3)}" fill="#555" text-anchor="middle">{rw:.1f}×{rh:.1f}m</text>')

    # 指北針
    nx,ny = width_px-38, height_px-38
    L.append(f'<circle cx="{nx}" cy="{ny}" r="16" fill="white" stroke="black" stroke-width="1"/>')
    L.append(f'<polygon points="{nx},{ny-13} {nx-4},{ny+2} {nx},{ny+13} {nx+4},{ny+2}" fill="black"/>')
    L.append(f'<polygon points="{nx},{ny-13} {nx-4},{ny+2} {nx},{ny+2} {nx+4},{ny+2}" fill="white"/>')
    L.append(f'<text x="{nx}" y="{ny-17}" font-family="{FONT}" font-size="9" text-anchor="middle">N</text>')

    # 比例尺
    bx,by = MARGIN, height_px-14
    sp = scale
    L.append(f'<line x1="{bx}" y1="{by}" x2="{bx+sp:.1f}" y2="{by}" stroke="black" stroke-width="1.5"/>')
    L.append(f'<line x1="{bx}" y1="{by-4}" x2="{bx}" y2="{by+4}" stroke="black" stroke-width="1.5"/>')
    L.append(f'<line x1="{bx+sp:.1f}" y1="{by-4}" x2="{bx+sp:.1f}" y2="{by+4}" stroke="black" stroke-width="1.5"/>')
    L.append(f'<text x="{bx+sp/2:.1f}" y="{by-7}" font-family="{FONT}" font-size="9" text-anchor="middle">1 m</text>')

    L.append('</svg>')
    return "\n".join(L)

# ==========================================
# DXF 產生器（支援不規則外牆）
# ==========================================
def generate_dxf(data):
    tw = data.get("total_width_m", 10)
    th = data.get("total_height_m", 8)
    rooms = data.get("rooms", [])
    doors = data.get("doors", [])
    windows = data.get("windows", [])
    outer = data.get("outer_polygon", [])

    doc = ezdxf.new(dxfversion="R2010")
    doc.units = ezdxf.units.M
    doc.layers.add("牆壁", color=7)
    doc.layers.add("門", color=3)
    doc.layers.add("窗戶", color=4)
    doc.layers.add("標註", color=2)
    msp = doc.modelspace()

    # 外牆多邊形（DXF y軸向上，所以 y 反轉）
    if outer and len(outer) >= 3:
        pts = [(p["x"], -p["y"]) for p in outer]
        pts.append(pts[0])  # 閉合
        msp.add_lwpolyline(pts, dxfattribs={"layer":"牆壁","lineweight":50})
    else:
        msp.add_lwpolyline(
            [(0,0),(tw,0),(tw,-th),(0,-th),(0,0)],
            dxfattribs={"layer":"牆壁","lineweight":50}
        )

    # 內牆
    for room in rooms:
        rx,ry,rw,rh = room.get("x",0),room.get("y",0),room.get("w",0),room.get("h",0)
        y = -ry
        msp.add_lwpolyline(
            [(rx,y),(rx+rw,y),(rx+rw,y-rh),(rx,y-rh),(rx,y)],
            dxfattribs={"layer":"牆壁","lineweight":25}
        )

    # 窗戶
    GAP = 0.12
    for win in windows:
        wx,wy,ww = win.get("x",0),win.get("y",0),win.get("width",1.0)
        wall = win.get("wall","top")
        if wall == "top":
            msp.add_line((wx,0),(wx+ww,0), dxfattribs={"layer":"窗戶"})
            msp.add_line((wx,-GAP),(wx+ww,-GAP), dxfattribs={"layer":"窗戶"})
            msp.add_line((wx,0),(wx,-GAP), dxfattribs={"layer":"窗戶"})
            msp.add_line((wx+ww,0),(wx+ww,-GAP), dxfattribs={"layer":"窗戶"})
        elif wall == "bottom":
            msp.add_line((wx,-th),(wx+ww,-th), dxfattribs={"layer":"窗戶"})
            msp.add_line((wx,-th+GAP),(wx+ww,-th+GAP), dxfattribs={"layer":"窗戶"})
            msp.add_line((wx,-th),(wx,-th+GAP), dxfattribs={"layer":"窗戶"})
            msp.add_line((wx+ww,-th),(wx+ww,-th+GAP), dxfattribs={"layer":"窗戶"})
        elif wall == "left":
            msp.add_line((0,-wy),(0,-wy-ww), dxfattribs={"layer":"窗戶"})
            msp.add_line((GAP,-wy),(GAP,-wy-ww), dxfattribs={"layer":"窗戶"})
            msp.add_line((0,-wy),(GAP,-wy), dxfattribs={"layer":"窗戶"})
            msp.add_line((0,-wy-ww),(GAP,-wy-ww), dxfattribs={"layer":"窗戶"})
        elif wall == "right":
            msp.add_line((tw,-wy),(tw,-wy-ww), dxfattribs={"layer":"窗戶"})
            msp.add_line((tw-GAP,-wy),(tw-GAP,-wy-ww), dxfattribs={"layer":"窗戶"})
            msp.add_line((tw,-wy),(tw-GAP,-wy), dxfattribs={"layer":"窗戶"})
            msp.add_line((tw,-wy-ww),(tw-GAP,-wy-ww), dxfattribs={"layer":"窗戶"})

    # 門
    for door in doors:
        dx,dy,dw = door.get("x",0),door.get("y",0),door.get("width",0.9)
        wall = door.get("wall","top")
        if wall == "top":
            msp.add_arc(center=(dx,0), radius=dw, start_angle=270, end_angle=360, dxfattribs={"layer":"門"})
            msp.add_line((dx,0),(dx,-dw), dxfattribs={"layer":"門"})
        elif wall == "bottom":
            msp.add_arc(center=(dx,-th), radius=dw, start_angle=0, end_angle=90, dxfattribs={"layer":"門"})
            msp.add_line((dx,-th),(dx,-th+dw), dxfattribs={"layer":"門"})
        elif wall == "left":
            msp.add_arc(center=(0,-dy), radius=dw, start_angle=0, end_angle=90, dxfattribs={"layer":"門"})
            msp.add_line((0,-dy),(dw,-dy), dxfattribs={"layer":"門"})
        elif wall == "right":
            msp.add_arc(center=(tw,-dy), radius=dw, start_angle=90, end_angle=180, dxfattribs={"layer":"門"})
            msp.add_line((tw,-dy),(tw-dw,-dy), dxfattribs={"layer":"門"})

    # 房間標註
    for room in rooms:
        rx,ry,rw,rh = room.get("x",0),room.get("y",0),room.get("w",0),room.get("h",0)
        cx,cy = rx+rw/2, -(ry+rh/2)
        th_txt = min(rw,rh)*0.12
        th_txt = max(0.15, min(th_txt, 0.35))
        msp.add_text(room.get("name",""), dxfattribs={"layer":"標註","height":th_txt}).set_placement(
            (cx, cy+th_txt*0.6), align=TextEntityAlignment.MIDDLE_CENTER)
        msp.add_text(f"{rw:.1f}x{rh:.1f}m", dxfattribs={"layer":"標註","height":th_txt*0.7}).set_placement(
            (cx, cy-th_txt*0.6), align=TextEntityAlignment.MIDDLE_CENTER)

    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix='.dxf', delete=False) as tmp:
        tmp_path = tmp.name
    doc.saveas(tmp_path)
    with open(tmp_path, 'rb') as f:
        dxf_data = f.read()
    os.unlink(tmp_path)
    return dxf_data

# ==========================================
# UI
# ==========================================
st.markdown("""
<div style='background:#222;padding:16px 24px;border-radius:6px;margin-bottom:24px'>
  <h1 style='color:white;margin:0;font-size:22px'>📐 貝克裝修｜平面圖產生器</h1>
  <p style='color:#aaa;margin:4px 0 0 0;font-size:12px'>上傳 GoodNotes PDF → AI 讀取尺寸 → 自動產出黑白平面圖（SVG + DXF）</p>
</div>
""", unsafe_allow_html=True)

with st.sidebar:
    st.header("⚙️ 設定")
    api_key = st.text_input("Anthropic API Key", type="password", placeholder="sk-ant-api03-...")
    if api_key: st.success("✅ API Key 已設定")
    else: st.warning("請輸入 API Key")
    st.divider()
    st.markdown("**手繪圖標註建議**")
    st.markdown("""
- 每個空間寫上名稱（客廳、主臥…）
- 每個轉角都標尺寸
- 冷媒管封板用螢光筆標示
- 尺寸單位：公分
    """)
    st.divider()
    st.caption("貝克室內裝修有限公司｜內部使用")

st.header("📤 上傳手繪平面圖")
uploaded_pdf = st.file_uploader("上傳 GoodNotes PDF", type=["pdf"])

if uploaded_pdf:
    with st.spinner("載入預覽..."):
        pdf_bytes = uploaded_pdf.read()
        previews = pdf_to_images_b64(pdf_bytes, dpi=80)
        cols = st.columns(min(len(previews), 4))
        for i, img_b64 in enumerate(previews[:4]):
            with cols[i]:
                st.image(base64.b64decode(img_b64), caption=f"第{i+1}頁", use_container_width=True)

    if st.button("🤖 分析並產出平面圖", type="primary", use_container_width=True):
        if not api_key:
            st.error("請先輸入 API Key")
        else:
            with st.spinner("AI 分析中，約需 30~50 秒..."):
                try:
                    uploaded_pdf.seek(0)
                    imgs = pdf_to_images_b64(uploaded_pdf.read(), dpi=150)[:8]
                    data = extract_floor_plan(api_key, imgs)
                    st.session_state["floor_data"] = data
                    st.success("✅ 分析完成")
                except Exception as e:
                    st.error(f"分析失敗：{e}")

if "floor_data" in st.session_state:
    data = st.session_state["floor_data"]
    st.divider()
    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader("📐 平面圖")
        svg = generate_svg(data, width_px=640)
        ratio = data.get("total_height_m",8) / max(data.get("total_width_m",10), 0.1)
        svg_h = int(ratio * 640) + 130
        st.components.v1.html(
            f'<div style="background:white;padding:8px;border:1px solid #ddd;display:inline-block">{svg}</div>',
            height=svg_h
        )
        c1, c2 = st.columns(2)
        with c1:
            st.download_button("⬇️ 下載 SVG", data=svg.encode("utf-8"),
                file_name="貝克平面圖.svg", mime="image/svg+xml",
                type="primary", use_container_width=True)
        with c2:
            try:
                dxf_bytes = generate_dxf(data)
                st.download_button("⬇️ 下載 DXF", data=dxf_bytes,
                    file_name="貝克平面圖.dxf", mime="application/dxf",
                    type="secondary", use_container_width=True)
            except Exception as e:
                st.warning(f"DXF 產生失敗：{e}")

    with col2:
        st.subheader("📋 解析結果")
        rooms = data.get("rooms", [])
        outer = data.get("outer_polygon", [])
        st.metric("偵測房間數", len(rooms))
        st.metric("總寬", f"{data.get('total_width_m',0):.1f} m")
        st.metric("總深", f"{data.get('total_height_m',0):.1f} m")
        st.metric("外牆頂點數", len(outer))
        if rooms:
            st.markdown("**房間清單**")
            for r in rooms:
                area = r.get("w",0)*r.get("h",0)
                st.markdown(f"- {r['name']}：{r.get('w',0):.1f}×{r.get('h',0):.1f}m（{area:.1f}㎡）")
        if data.get("notes"):
            st.info(data["notes"])

        st.divider()
        st.caption("尺寸有誤可直接修改 JSON 後重新產圖")
        edited = st.text_area("JSON", value=json.dumps(data, ensure_ascii=False, indent=2),
                              height=300, label_visibility="collapsed")
        if st.button("🔄 重新產圖", use_container_width=True):
            try:
                st.session_state["floor_data"] = json.loads(edited)
                st.rerun()
            except Exception as e:
                st.error(f"JSON格式錯誤：{e}")

st.markdown("---")
st.markdown("<p style='text-align:center;color:#aaa;font-size:11px'>貝克室內裝修有限公司｜平面圖產生器｜內部使用</p>", unsafe_allow_html=True)
