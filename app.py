"""쇼핑 리스트 앱 (Streamlit).

PRD.md 기준. 실행: streamlit run app.py
"""

import html
import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="쇼핑 리스트", page_icon="🛒", layout="centered")

DATA_FILE = Path(__file__).parent / "shopping_list.json"


# ---------------------------------------------------------------------------
# 저장 / 불러오기 (F-6)
#
# 파일이 유일한 원본이다. 탭(세션)마다 목록을 따로 들고 있다가 통째로 덮어쓰면
# 다른 탭의 변경이 사라지므로, 화면을 그릴 때와 변경할 때마다 파일을 다시 읽는다.
# ---------------------------------------------------------------------------

@st.cache_resource
def _file_lock() -> threading.Lock:
    """모든 세션이 공유하는 파일 잠금 (스크립트가 재실행돼도 같은 객체)."""
    return threading.Lock()


def _normalize(data: list) -> tuple[list[dict], bool]:
    """파일 내용을 PRD 5장 데이터 모델로 정리한다. 정리하면서 바뀐 게 있으면 changed=True."""
    items, seen, changed = [], set(), False
    for raw in data:
        if not isinstance(raw, dict) or not str(raw.get("name", "")).strip():
            changed = True
            continue
        try:
            quantity = max(1, int(raw.get("quantity", 1)))
        except (TypeError, ValueError):
            quantity = 1
        item_id = str(raw.get("id") or "")
        if not item_id or item_id in seen:  # id가 없거나 겹치면 위젯 key가 충돌하므로 새로 발급
            item_id = str(uuid.uuid4())
        seen.add(item_id)
        item = {
            "id": item_id,
            "name": str(raw["name"]).strip(),
            "quantity": quantity,
            "checked": raw.get("checked") is True,  # "false" 같은 문자열을 참으로 읽지 않도록
            "created_at": str(raw.get("created_at", "")),
        }
        changed = changed or item != raw
        items.append(item)
    return items, changed


def _quarantine_corrupt_file() -> None:
    """읽을 수 없는 저장 파일을 지우지 않고 옆에 백업해 둔다."""
    backup = DATA_FILE.with_name(f"shopping_list.corrupt-{datetime.now():%Y%m%d-%H%M%S}.json")
    try:
        DATA_FILE.replace(backup)
        set_message("warning", f"저장 파일이 손상되어 {backup.name}(으)로 옮겨 두고 빈 리스트로 시작합니다.")
    except OSError:
        set_message("error", "손상된 저장 파일을 백업하지 못했습니다.")


def _write_file(items: list[dict]) -> None:
    """임시 파일에 쓴 뒤 교체한다. 쓰는 도중 중단돼도 기존 파일이 깨지지 않는다."""
    tmp = DATA_FILE.with_name(DATA_FILE.name + ".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, DATA_FILE)


def _read_file() -> list[dict]:
    """잠금을 잡은 상태에서 호출한다."""
    if not DATA_FILE.exists():
        return []
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except OSError:
        set_message("error", "저장 파일을 읽을 수 없습니다.")
        return []
    except ValueError:
        _quarantine_corrupt_file()
        return []
    if not isinstance(data, list):
        _quarantine_corrupt_file()
        return []
    items, changed = _normalize(data)
    if changed:
        try:
            _write_file(items)  # 정리한 내용을 저장해 다음 실행에서도 같은 id를 쓰게 한다
        except OSError:
            pass
    return items


def load_items() -> list[dict]:
    """shopping_list.json에서 아이템을 불러온다. 파일이 없거나 깨졌으면 빈 리스트."""
    with _file_lock():
        return _read_file()


@contextmanager
def edit_items():
    """파일에서 최신 목록을 읽어 수정하게 하고, 끝나면 저장한다.

    with edit_items() as items:
        items.append(...)
    """
    with _file_lock():
        items = _read_file()
        yield items
        try:
            _write_file(items)
        except OSError as e:
            set_message("error", f"저장에 실패했습니다: {e}")
        st.session_state["items"] = items


# ---------------------------------------------------------------------------
# 상태 / 데이터 모델
# ---------------------------------------------------------------------------

def init_state() -> None:
    """세션 상태를 초기화하고, 다른 탭의 변경이 보이도록 매 실행마다 파일에서 목록을 새로 읽는다."""
    if "messages" not in st.session_state:
        st.session_state.messages = []  # list[tuple[str, str]] — 다음 렌더링 때 보여줄 알림
    if "editing_id" not in st.session_state:
        st.session_state.editing_id = None  # str | None
    st.session_state["items"] = load_items()  # list[dict]
    if st.session_state.editing_id and find_item(st.session_state.editing_id) is None:
        st.session_state.editing_id = None  # 다른 탭에서 지워진 항목이면 편집 해제


def create_item(name: str, quantity: int = 1) -> dict:
    """PRD 5장 데이터 모델에 맞는 아이템 딕셔너리를 만든다."""
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "quantity": quantity,
        "checked": False,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def find_item(item_id: str, items: list[dict] | None = None) -> dict | None:
    if items is None:
        items = st.session_state.get("items", [])
    return next((it for it in items if it["id"] == item_id), None)


def set_message(kind: str, text: str) -> None:
    """콜백 안에서 만든 알림을 다음 렌더링 때 보여주도록 저장한다. kind: warning / info / error"""
    st.session_state.messages.append((kind, text))


# ---------------------------------------------------------------------------
# 기능 로직
# ---------------------------------------------------------------------------

def add_item(name: str, quantity: int = 1) -> bool:
    """새 아이템을 리스트 끝에 추가한다. 이름이 비어 있으면 추가하지 않고 False를 반환한다. (F-1)"""
    name = name.strip()
    if not name:
        set_message("warning", "아이템 이름을 입력하세요.")
        return False
    with edit_items() as items:
        if any(it["name"] == name for it in items):
            set_message("info", f"'{name}'은(는) 이미 리스트에 있습니다. 그래도 추가했어요.")
        items.append(create_item(name, max(1, int(quantity))))
    return True


def update_item(item_id: str, name: str, quantity: int) -> bool:
    """id에 해당하는 아이템의 이름과 수량을 수정한다. 이름이 비어 있으면 False를 반환한다. (F-2)"""
    name = name.strip()
    if not name:
        set_message("warning", "아이템 이름을 입력하세요.")
        return False
    with edit_items() as items:
        item = find_item(item_id, items)
        if item is None:
            set_message("warning", "다른 곳에서 이미 삭제된 아이템입니다.")
            return False
        item["name"] = name
        item["quantity"] = max(1, int(quantity))
    return True


def delete_item(item_id: str) -> None:
    """id에 해당하는 아이템을 삭제한다. 편집 중이던 아이템이면 editing_id도 초기화한다. (F-3)"""
    with edit_items() as items:
        items[:] = [it for it in items if it["id"] != item_id]
    if st.session_state.editing_id == item_id:
        st.session_state.editing_id = None


def toggle_item(item_id: str, checked: bool | None = None) -> None:
    """id에 해당하는 아이템의 체크 상태를 바꾼다. checked를 주면 그 값으로, 없으면 반전. (F-4)"""
    with edit_items() as items:
        item = find_item(item_id, items)
        if item is not None:
            item["checked"] = (not item["checked"]) if checked is None else bool(checked)


def clear_checked() -> None:
    """체크된 아이템을 모두 삭제한다. (F-3 선택 기능)"""
    with edit_items() as items:
        removed = {it["id"] for it in items if it["checked"]}
        items[:] = [it for it in items if not it["checked"]]
    if st.session_state.editing_id in removed:
        st.session_state.editing_id = None


# ---------------------------------------------------------------------------
# 위젯 콜백 — 상태 변경은 모두 여기서 처리해 한 번 클릭으로 바로 반영되게 한다
# ---------------------------------------------------------------------------

def on_add_submit() -> None:
    add_item(st.session_state.get("new_name", ""), st.session_state.get("new_qty", 1))


def on_check_change(item_id: str) -> None:
    # 반전이 아니라 체크박스의 현재 값으로 저장 — 다른 탭에서 상태가 바뀌었어도 사용자가 본 대로 반영
    toggle_item(item_id, st.session_state.get(f"check_{item_id}", False))


def on_start_edit(item_id: str) -> None:
    item = find_item(item_id)
    if item is None:
        return
    prev_id = st.session_state.editing_id
    prev = find_item(prev_id) if prev_id and prev_id != item_id else None
    if prev is not None:
        new_name = str(st.session_state.get(f"edit_name_{prev_id}", prev["name"])).strip()
        new_qty = st.session_state.get(f"edit_qty_{prev_id}", prev["quantity"])
        if new_name != prev["name"] or new_qty != prev["quantity"]:
            set_message("info", f"저장하지 않은 '{prev['name']}'의 수정 내용은 취소했어요.")
    # 이전 편집에서 남은 입력값 대신 현재 값으로 편집창을 채운다
    st.session_state[f"edit_name_{item_id}"] = item["name"]
    st.session_state[f"edit_qty_{item_id}"] = item["quantity"]
    st.session_state.editing_id = item_id


def on_save_edit(item_id: str) -> None:
    ok = update_item(
        item_id,
        st.session_state.get(f"edit_name_{item_id}", ""),
        st.session_state.get(f"edit_qty_{item_id}", 1),
    )
    if ok:
        st.session_state.editing_id = None


def on_cancel_edit() -> None:
    st.session_state.editing_id = None


# ---------------------------------------------------------------------------
# 화면 영역
# ---------------------------------------------------------------------------

# 색은 테마 기본값(.streamlit/config.toml의 primaryColor)과 반투명 값만 써서
# 라이트/다크 테마 모두에서 글자가 보이도록 한다.
CUSTOM_CSS = """
<style>
@import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css");

.stApp, .stApp p, .stApp label, .stApp input, .stApp button,
.stApp h1, .stApp h2, .stApp h3, [data-testid="stMetricValue"] {
    font-family: "Pretendard", "Noto Sans KR", -apple-system, sans-serif !important;
}

/* Streamlit 기본 메뉴·툴바·푸터 숨기기 */
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] { display: none !important; }
.block-container { padding-top: 2.5rem; max-width: 640px; }

/* 헤더 */
.stApp .app-title { font-size: 2rem !important; font-weight: 800; line-height: 1.25; margin: 0; letter-spacing: -0.02em; }
.stApp .app-subtitle { opacity: 0.6; margin: 0.25rem 0 1.25rem; }

/* 요약 카드 */
[data-testid="stMetric"] { text-align: center; }
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] > div {
    display: flex !important; justify-content: center !important; width: 100%;
}
[data-testid="stMetricValue"] { font-weight: 700; }

/* 아이템 카드 */
[class*="st-key-row_"] {
    border-radius: 12px !important;
    padding: 0.35rem 0.75rem !important;
    transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
[class*="st-key-row_"]:hover {
    border-color: rgba(46, 125, 50, 0.55) !important;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}
[class*="st-key-row_"] p { margin: 0; }
/* Streamlit 마크다운의 기본 음수 하단 여백 때문에 이름이 체크박스보다 아래로 처지는 것 보정 */
[class*="st-key-row_"] [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }
[class*="st-key-row_done_"] { opacity: 0.55; }

/* 아이템 이름 + 수량 배지 (사용자 입력은 HTML 이스케이프해서 그대로 출력) */
.item-line { display: flex; align-items: center; flex-wrap: wrap; gap: 0.25rem 0.6rem; min-width: 0; }
.item-name { font-weight: 600; overflow-wrap: anywhere; }
.item-name.done { text-decoration: line-through; font-weight: 400; }
.qty-badge {
    font-size: 0.8rem; font-weight: 600; line-height: 1.5; padding: 0 0.45rem; border-radius: 6px;
    color: #3f9a45; background: rgba(76, 175, 80, 0.16); white-space: nowrap;
}

/* 모바일에서도 한 줄 유지 — Streamlit은 좁은 화면에서 columns를 세로로 쌓는다 */
[class*="st-key-row_"] [data-testid="stHorizontalBlock"],
.st-key-add_row [data-testid="stHorizontalBlock"],
.st-key-summary [data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap !important;
    flex-direction: row !important;
    gap: 0.5rem !important;
}
[class*="st-key-row_"] [data-testid="stColumn"],
.st-key-add_row [data-testid="stColumn"],
.st-key-summary [data-testid="stColumn"] {
    min-width: 0 !important;
    width: auto !important;
}
[class*="st-key-row_todo_"] [data-testid="stColumn"]:has(button),
[class*="st-key-row_done_"] [data-testid="stColumn"]:has(button),
[class*="st-key-row_"] [data-testid="stColumn"]:has([data-testid="stCheckbox"]) {
    flex: 0 0 auto !important;
}

/* 아이콘 버튼 — .stButton으로 한정해 편집창 입력칸(edit_name_/edit_qty_)에는 적용되지 않게 */
[class*="st-key-edit_"] .stButton button:hover { background: rgba(46, 125, 50, 0.12) !important; }
[class*="st-key-del_"] .stButton button:hover { background: rgba(211, 47, 47, 0.14) !important; }
/* 버튼 글자("수정"/"삭제")는 화면에서만 숨겨 아이콘만 보이게 하고, 화면 낭독기는 읽게 한다 */
[class*="st-key-edit_"] .stButton button [data-testid="stMarkdownContainer"],
[class*="st-key-del_"] .stButton button [data-testid="stMarkdownContainer"] {
    position: absolute !important; width: 1px; height: 1px; overflow: hidden;
    clip: rect(0 0 0 0); clip-path: inset(50%); white-space: nowrap;
}
.stButton button, .stFormSubmitButton button { transition: transform 0.08s ease, background 0.15s ease; }
.stButton button:active, .stFormSubmitButton button:active { transform: scale(0.96); }

/* 빈 리스트 안내 */
.empty-state { text-align: center; padding: 2.5rem 0 2rem; opacity: 0.65; }
.empty-state .icon { font-size: 2.75rem; line-height: 1; }
.empty-state p { margin: 0.6rem 0 0; }

/* 작은 화면 */
@media (max-width: 480px) {
    .block-container { padding-left: 1rem; padding-right: 1rem; padding-top: 1.5rem; }
    .stApp .app-title { font-size: 1.6rem !important; }
    [data-testid="stMetricValue"] { font-size: 1.5rem; }
}
</style>
"""


def render_header() -> None:
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
    st.markdown(
        '<p class="app-title">🛒 쇼핑 리스트</p>'
        '<p class="app-subtitle">오늘 살 것을 적어보세요</p>',
        unsafe_allow_html=True,
    )


def render_messages() -> None:
    show = {"warning": st.warning, "info": st.info, "error": st.error}
    for kind, text in st.session_state.messages:
        show.get(kind, st.info)(text)
    st.session_state.messages = []


def render_input_form() -> None:
    """아이템 이름·수량 입력 폼과 [추가] 버튼."""
    with st.form("add_form", clear_on_submit=True):
        with st.container(key="add_row"):
            col_name, col_qty, col_btn = st.columns([5, 1.6, 1.4], vertical_alignment="bottom")
            col_name.text_input("아이템 이름", key="new_name", placeholder="예: 우유")
            col_qty.number_input("수량", key="new_qty", min_value=1, value=1, step=1)
            col_btn.form_submit_button(
                "추가", on_click=on_add_submit, type="primary", use_container_width=True
            )
    render_messages()


def render_summary() -> None:
    """전체 / 남은 / 완료 개수 요약 + 진행률."""
    items = st.session_state["items"]
    done = sum(it["checked"] for it in items)
    with st.container(key="summary"):
        c1, c2, c3 = st.columns(3)
        c1.metric("전체", len(items), border=True)
        c2.metric("남은", len(items) - done, border=True)
        c3.metric("완료", done, border=True)
    if items:
        pct = round(done / len(items) * 100)
        st.progress(done / len(items), text=f"진행률 {pct}%  ·  {done}/{len(items)} 완료")


def render_item_row(item: dict) -> None:
    item_id = item["id"]
    if st.session_state.editing_id == item_id:
        with st.container(border=True, key=f"row_edit_{item_id}"):
            col_name, col_qty = st.columns([5, 1.6], vertical_alignment="bottom")
            col_name.text_input("이름 수정", key=f"edit_name_{item_id}")
            col_qty.number_input("수량", key=f"edit_qty_{item_id}", min_value=1, step=1)
            _, col_cancel, col_save = st.columns([4, 1.3, 1.3])
            col_cancel.button("취소", key=f"cancel_{item_id}", on_click=on_cancel_edit, use_container_width=True)
            col_save.button(
                "저장", key=f"save_{item_id}", on_click=on_save_edit, args=(item_id,),
                type="primary", use_container_width=True,
            )
        return

    state = "done" if item["checked"] else "todo"
    with st.container(border=True, key=f"row_{state}_{item_id}"):
        col_check, col_name, col_edit, col_del = st.columns([1, 6, 1, 1], vertical_alignment="center")
        # 파일(다른 탭 포함)의 최신 상태를 체크박스에 반영. value= 대신 위젯 생성 전에 상태를 직접 넣는다.
        st.session_state[f"check_{item_id}"] = item["checked"]
        col_check.checkbox(
            # 라벨은 화면에 표시되지 않고(display:none) 원문 그대로 aria-label로 쓰인다.
            # 마크다운 이스케이프를 하면 화면 낭독기가 역슬래시까지 읽으므로 원문을 넘긴다.
            item["name"],
            key=f"check_{item_id}",
            on_change=on_check_change,
            args=(item_id,),
            label_visibility="collapsed",
        )
        # 마크다운을 거치지 않도록 HTML 이스케이프 후 HTML 블록으로 출력 (URL 자동 링크, &lt; 해석 방지)
        name_cls = "item-name done" if item["checked"] else "item-name"
        col_name.markdown(
            f'<div class="item-line"><span class="{name_cls}">{html.escape(item["name"])}</span> '
            f'<span class="qty-badge">x{item["quantity"]}</span></div>',
            unsafe_allow_html=True,
        )
        col_edit.button(
            "수정", key=f"edit_{item_id}", on_click=on_start_edit, args=(item_id,),
            icon=":material/edit:", help="수정", type="tertiary",
        )
        col_del.button(
            "삭제", key=f"del_{item_id}", on_click=delete_item, args=(item_id,),
            icon=":material/delete:", help="삭제", type="tertiary",
        )


def render_item_list() -> None:
    """아이템 목록 (체크박스, 이름, 수정, 삭제). 남은 항목이 위, 완료 항목이 아래."""
    items = st.session_state["items"]
    if not items:
        st.markdown(
            '<div class="empty-state"><div class="icon">🧺</div>'
            "<p>쇼핑 리스트가 비어 있습니다.</p>"
            "<p><small>위 입력창에 살 물건을 추가해 보세요.</small></p></div>",
            unsafe_allow_html=True,
        )
        return
    # 화면 표시만 정렬하고 저장 순서는 그대로 둔다 (sorted는 안정 정렬이라 같은 그룹 안의 순서 유지)
    for item in sorted(items, key=lambda it: it["checked"]):
        render_item_row(item)


def render_footer() -> None:
    """하단 영역 ([완료 항목 모두 삭제] 버튼). 리스트가 비어 있으면 숨긴다."""
    items = st.session_state["items"]
    if not items:
        return
    st.button(
        "완료 항목 모두 삭제", on_click=clear_checked,
        disabled=not any(it["checked"] for it in items),
        icon=":material/delete_sweep:", use_container_width=True,
    )


def main() -> None:
    init_state()
    render_header()
    render_input_form()
    render_summary()
    render_item_list()
    render_footer()


main()
