"""
AI记账助手 - V1.1
侧边栏三模块：记账 / 历史记账 / 记账统计
"""
import streamlit as st
import pandas as pd
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

# 北京时间（UTC+8）
CN_TZ = timezone(timedelta(hours=8))

def now_cn():
    """返回当前北京时间"""
    return datetime.now(CN_TZ).replace(tzinfo=None)
from openai import OpenAI
import plotly.graph_objects as go

# ============================================================
# 页面配置
# ============================================================
st.set_page_config(
    page_title="AI记账助手",
    page_icon="🧾",
    layout="wide"
)

# 让按钮支持多行文字（emoji在上，文字在下）
st.html("""
<style>
.stButton button {
    white-space: pre-line !important;
    line-height: 1.3 !important;
}
</style>
""")

# 滚动位置记忆：rerun 后恢复主区域滚动位置，避免聊天区"跳动"
st.components.v1.html("""
<script>
(function() {
    const KEY = "__scroll_memory__";
    const doc = window.parent.document;
    const win = window.parent;
    // 页面加载完成后恢复滚动位置
    if (win[KEY] !== undefined) {
        const target = win[KEY];
        win[KEY] = undefined;
        // 等 DOM 渲染完再恢复
        setTimeout(() => {
            const main = doc.querySelector('section.main')
                      || doc.querySelector('[data-testid="stAppViewContainer"]')
                      || doc.documentElement;
            if (main) main.scrollTop = target;
            win.scrollTo(0, target);
        }, 100);
    }
    // 页面即将 rerun（卸载）前记录滚动位置
    win.addEventListener('beforeunload', () => {
        const main = doc.querySelector('section.main')
                  || doc.querySelector('[data-testid="stAppViewContainer"]');
        win[KEY] = main ? main.scrollTop : win.scrollY;
    });
})();
</script>
""", height=0)

# ============================================================
# 分类体系（产品方案定义的二级结构）
# ============================================================
CATEGORY_MAP = {
    "🍔 餐饮": ["🍚 三餐正餐", "🥤 奶茶咖啡", "🍰 零食甜品", "🍻 聚餐社交", "🛒 买菜食材", "🍉 水果"],
    "🚇 交通出行": ["🚌 公共交通", "🚗 打车/网约车", "⛽ 加油/充电", "🅿️ 停车费"],
    "🏠 居住日常": ["🏠 房租/房贷", "💡 水电燃气", "🧹 日用品", "📦 物业/维修"],
    "👗 购物消费": ["👔 服饰鞋包", "💄 美妆护肤", "📱 数码3C", "🛋️ 家居百货"],
    "🎮 休闲娱乐": ["🎬 电影/演出", "🎮 游戏充值", "🏋️ 运动健身", "✈️ 旅行度假"],
    "💊 医疗健康": ["🏥 看病配药", "💪 保健品", "🦷 牙科/体检"],
    "📚 学习成长": ["📖 书籍/课程", "🎓 培训/考试", "📝 文具/工具"],
    "💰 收入": ["💼 工资薪酬", "🎁 红包/礼金", "📈 理财收益", "💰 兼职/副业"],
    "🔄 其他": ["🔄 转账/还款", "❓ 其他"],
}

# 构建AI可用的分类列表
CATEGORY_FLAT = []
for main_cat, sub_cats in CATEGORY_MAP.items():
    for sub_cat in sub_cats:
        CATEGORY_FLAT.append(f"{main_cat} > {sub_cat}")

# ============================================================
# DeepSeek API 配置
# ============================================================
SYSTEM_PROMPT = f"""你是一个专业的记账助手。用户会用自然语言描述消费或收入，你需要解析成结构化数据。

## 输出格式
必须返回严格的 JSON，不要包含任何其他文字：
{{"amount": 数字, "type": "支出或收入", "category_sub": "二级分类", "note": "简短备注", "time": "HH:MM"}}

## 分类规则
可选二级分类（必须从以下列表中选择最匹配的一个）：
{', '.join(CATEGORY_FLAT)}

## 时间推理规则
- "刚才"/"刚刚" → 当前时间
- "中午"/"午饭" → 12:00
- "早上"/"早餐" → 08:00
- "晚上"/"晚饭" → 19:00
- "下午" → 15:00
- "昨天" → 不返回time字段，返回 date_offset: -1
- 没有时间信息的 → 使用当前时间

## 金额规则
- "35块"/"35元"/"35" → 35.00
- "三十五" → 35.00
- 金额必须为正数

## type 判断规则
- 提到工资、红包收入、兼职收入、退款 → "收入"
- 其余情况 → "支出"

## 备注规则
- 从用户原话中提取关键信息（地点、物品、原因），简短即可
- 不要超过10个字

## 示例
输入："中午食堂花了35"
输出：{{"amount": 35.00, "type": "支出", "category_sub": "🍚 三餐正餐", "note": "食堂午餐", "time": "12:00"}}

输入："刚发了工资8000"
输出：{{"amount": 8000.00, "type": "收入", "category_sub": "💼 工资薪酬", "note": "发工资", "time": "09:00"}}

输入："打车去公司26.5"
输出：{{"amount": 26.50, "type": "支出", "category_sub": "🚗 打车/网约车", "note": "去公司", "time": "09:00"}}

输入："昨天买奶茶18块"
输出：{{"amount": 18.00, "type": "支出", "category_sub": "🥤 奶茶咖啡", "note": "买奶茶", "date_offset": -1}}

## 无法解析时
- 如果用户没有提到具体金额，返回：{{"error": "no_amount"}}
- 如果完全无法理解消费意图，返回：{{"error": "unknown"}}
"""


def get_client():
    """获取 DeepSeek API 客户端"""
    api_key = st.secrets.get("DEEPSEEK_API_KEY", None)
    if not api_key:
        return None
    return OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com/v1"
    )


def _has_amount_hint(user_text: str) -> bool:
    """预检：用户输入是否包含金额相关信息"""
    # 阿拉伯数字
    if re.search(r"\d+", user_text):
        return True
    # 中文数字
    if re.search(r"[一二三四五六七八九十百千万两零]", user_text):
        return True
    # 金额单位
    if re.search(r"[块元角分毛]", user_text):
        return True
    return False


def parse_input(user_text: str, base_date: datetime = None) -> Optional[dict]:
    """调用 DeepSeek 解析用户输入，返回结构化数据或 None
    base_date: 用户选择的记账日期，未传则默认今天"""
    # 预检：没有金额线索就不调 API，省钱且避免 AI 编造
    if not _has_amount_hint(user_text):
        st.session_state.parse_error = "no_amount"
        return None

    client = get_client()
    if not client:
        return None

    if base_date is None:
        base_date = now_cn()
    now = base_date
    time_hint = f"当前时间是 {now.strftime('%H:%M')}，日期是 {now.strftime('%Y-%m-%d')}。"

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"{time_hint}\n用户说：{user_text}"}
            ],
            temperature=0.1,
            max_tokens=300
        )

        raw = response.choices[0].message.content.strip()

        # 尝试提取 JSON（处理可能的 markdown 代码块包裹）
        json_match = re.search(r'\{[^{}]*\}', raw, re.DOTALL)
        if json_match:
            raw = json_match.group(0)

        result = json.loads(raw)

        # AI 返回错误信号：无法解析
        if "error" in result:
            if result["error"] == "no_amount":
                st.session_state.parse_error = "no_amount"
            else:
                st.session_state.parse_error = "unknown"
            return None

        # 校验必填字段
        if "amount" not in result or "type" not in result or "category_sub" not in result:
            return None

        # 金额为 0 或负数 → 视为无效，不记账
        if float(result["amount"]) <= 0:
            st.session_state.parse_error = "no_amount"
            return None

        # 处理时间
        note = result.get("note", "")[:10]  # 截断备注到10字
        date_offset = result.get("date_offset", 0)
        record_date = now + timedelta(days=date_offset)

        if "time" in result:
            hour, minute = result["time"].split(":")
            record_time = record_date.replace(
                hour=int(hour), minute=int(minute), second=0, microsecond=0
            )
        else:
            record_time = record_date.replace(second=0, microsecond=0)

        # 从 category_sub 反查 category_main
        category_main = "🔄 其他"
        for main_cat, sub_cats in CATEGORY_MAP.items():
            if result["category_sub"] in sub_cats:
                category_main = main_cat
                break

        return {
            "amount": float(result["amount"]),
            "type": result["type"],
            "category_main": category_main,
            "category_sub": result["category_sub"],
            "note": note,
            "timestamp": record_time
        }

    except (json.JSONDecodeError, KeyError, ValueError, AttributeError):
        return None
    except Exception:
        return None


# ============================================================
# 数据持久化：Supabase（云端主存储）+ JSON（本地备份）
# ============================================================
DATA_FILE = "records.json"
CHAT_FILE = "chat_history.json"


def get_supabase():
    """获取 Supabase 客户端。未配置则返回 None"""
    url = st.secrets.get("SUPABASE_URL", "")
    key = st.secrets.get("SUPABASE_KEY", "")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except ImportError:
        return None


def load_records():
    """加载记录：优先从 Supabase，失败则从本地 JSON 恢复"""
    sb = get_supabase()

    if sb:
        try:
            response = sb.table("records").select("*").execute()
            data = response.data
            if data:
                df = pd.DataFrame(data)
                if "timestamp" in df.columns:
                    df["timestamp"] = pd.to_datetime(df["timestamp"])
                # 同步到本地 JSON 做备份
                _save_json(df)
                return df
            else:
                # Supabase 表为空，检查本地是否有旧数据
                return _load_json()
        except Exception as e:
            st.sidebar.warning(f"⚠️ 云端连接失败，使用本地数据：{e}")
            return _load_json()
    else:
        return _load_json()


def _load_json():
    """从本地 JSON 加载，兼容未配置 Supabase 的情况"""
    if not os.path.exists(DATA_FILE):
        return None
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not data:
            return None
        df = pd.DataFrame(data)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        st.sidebar.warning(f"⚠️ 本地数据损坏，已重置。错误：{e}")
        return None


def _load_chat():
    """从本地 JSON 加载聊天记录"""
    if not os.path.exists(CHAT_FILE):
        return []
    try:
        with open(CHAT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, KeyError):
        return []


def _save_json(df=None):
    """保存 DataFrame 到本地 JSON（始终可用，作为备份）"""
    records = df.copy() if df is not None else st.session_state.records.copy()
    if "timestamp" in records.columns and len(records) > 0:
        records["timestamp"] = records["timestamp"].apply(
            lambda t: t.isoformat() if hasattr(t, "isoformat") else str(t)
        )
    data = records.to_dict(orient="records")
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # 同时保存聊天记录
    if "chat_history" in st.session_state:
        with open(CHAT_FILE, "w", encoding="utf-8") as f:
            json.dump(st.session_state.chat_history, f, ensure_ascii=False, indent=2)


def _row_to_dict(row: dict) -> dict:
    """将记录行转为 Supabase 兼容的字典（timestamp 转字符串）"""
    d = dict(row)
    if "timestamp" in d and hasattr(d["timestamp"], "isoformat"):
        d["timestamp"] = d["timestamp"].isoformat()
    return d


# ============================================================
# 初始化 session_state
# ============================================================
if "records" not in st.session_state:
    saved = load_records()
    if saved is not None and len(saved) > 0:
        st.session_state.records = saved
        max_id = 0
        for rid in saved["id"]:
            num = re.search(r"(\d+)", rid)
            if num:
                max_id = max(max_id, int(num.group(1)))
        st.session_state.record_counter = max_id
    else:
        st.session_state.records = pd.DataFrame(columns=[
            "id", "amount", "type", "category_main", "category_sub",
            "note", "timestamp"
        ])
        st.session_state.record_counter = 0

if "record_counter" not in st.session_state:
    st.session_state.record_counter = 0

if "error_msg" not in st.session_state:
    st.session_state.error_msg = None

if "parse_error" not in st.session_state:
    st.session_state.parse_error = None  # no_amount | unknown | None

if "confirm_delete_id" not in st.session_state:
    st.session_state.confirm_delete_id = None

if "last_added_id" not in st.session_state:
    st.session_state.last_added_id = None

if "chat_history" not in st.session_state:
    st.session_state.chat_history = _load_chat()  # [{user_text, record_id}]，持久化到JSON

if "editing_id" not in st.session_state:
    st.session_state.editing_id = None


def add_record(record_data: dict):
    """添加记录：写入 Supabase + 本地 DataFrame + JSON 备份"""
    st.session_state.record_counter += 1
    new_row = {
        "id": f"rec_{st.session_state.record_counter:04d}",
        "amount": record_data["amount"],
        "type": record_data["type"],
        "category_main": record_data["category_main"],
        "category_sub": record_data["category_sub"],
        "note": record_data["note"],
        "timestamp": record_data["timestamp"]
    }
    st.session_state.records = pd.concat([
        st.session_state.records,
        pd.DataFrame([new_row])
    ], ignore_index=True)

    sb = get_supabase()
    if sb:
        try:
            sb.table("records").insert(_row_to_dict(new_row)).execute()
        except Exception:
            pass  # 云端失败不影响本地使用
    _save_json()


def undo_last():
    """撤销最后一条：删除 Supabase 记录 + 本地 DataFrame + JSON 备份"""
    if len(st.session_state.records) > 0:
        last_row = st.session_state.records.iloc[-1]
        last_id = last_row["id"]

        sb = get_supabase()
        if sb:
            try:
                sb.table("records").delete().eq("id", last_id).execute()
            except Exception:
                pass

        st.session_state.records = st.session_state.records.iloc[:-1]
        st.session_state.record_counter -= 1
        _save_json()


def delete_record(record_id: str):
    """删除指定记录：删除 Supabase 记录 + 本地 DataFrame + JSON 备份"""
    sb = get_supabase()
    if sb:
        try:
            sb.table("records").delete().eq("id", record_id).execute()
        except Exception:
            pass

    records = st.session_state.records
    mask = records["id"] != record_id
    st.session_state.records = records[mask].reset_index(drop=True)
    _save_json()


def update_record(record_id: str, new_data: dict):
    """更新记录：更新 Supabase + 本地 DataFrame + JSON 备份"""
    sb = get_supabase()
    if sb:
        try:
            sb.table("records").update(_row_to_dict(new_data)).eq("id", record_id).execute()
        except Exception:
            pass

    records = st.session_state.records
    mask = records["id"] == record_id
    if mask.any():
        idx = records[mask].index[0]
        for key, value in new_data.items():
            if key in records.columns:
                records.at[idx, key] = value
        st.session_state.records = records
        _save_json()




@st.dialog("✏️ 编辑记录", width="large")
def edit_dialog(edit_row, edit_id):
    """编辑弹窗 - Streamlit 原生 Dialog，叠加在原界面上方"""
    # 标记弹窗已渲染：如果 ✕ 关闭弹窗，函数体不执行，此标记为 False → 调用方可清空 editing_id
    st.session_state._edit_dialog_rendered = True

    # ---- 金额 & 类型 ----
    col_a, col_b = st.columns(2)
    with col_a:
        new_amount = st.number_input(
            "💰 金额", value=float(edit_row["amount"]),
            min_value=0.01, step=0.01, format="%.2f",
            key="dialog_amount"
        )
    with col_b:
        new_type = st.radio(
            "📌 类型", ["支出", "收入"],
            index=0 if edit_row["type"] == "支出" else 1,
            horizontal=True,
            key="dialog_type"
        )

    # ---- 分类选择（根据类型过滤） ----
    if new_type == "收入":
        available_main = ["💰 收入"]
    else:
        available_main = [k for k in CATEGORY_MAP.keys() if k != "💰 收入"]

    default_main = edit_row["category_main"]
    if default_main not in available_main:
        default_main = available_main[0]

    col_c, col_d = st.columns(2)
    with col_c:
        new_main = st.selectbox(
            "📂 一级分类",
            available_main,
            index=available_main.index(default_main),
            key="dialog_main"
        )
    with col_d:
        new_sub_list = CATEGORY_MAP[new_main]
        default_sub = edit_row["category_sub"]
        if default_sub not in new_sub_list:
            default_sub = new_sub_list[0]
        new_sub = st.selectbox(
            "🏷️ 二级分类",
            new_sub_list,
            index=new_sub_list.index(default_sub),
            key="dialog_sub"
        )

    # ---- 备注 ----
    raw_note = edit_row["note"]
    note_val = str(raw_note) if raw_note and str(raw_note) != "nan" else ""
    new_note = st.text_input(
        "📝 备注",
        value=note_val,
        key="dialog_note",
        placeholder="选填，简短描述…"
    )

    # ---- 日期 & 时间 ----
    ts = pd.to_datetime(edit_row["timestamp"])
    col_e, col_f = st.columns(2)
    with col_e:
        new_date = st.date_input("📅 日期", value=ts.date(), key="dialog_date")
    with col_f:
        new_time = st.time_input("⏰ 时间", value=ts.time(), key="dialog_time")

    st.divider()

    # ---- 操作按钮 ----
    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        if st.button("💾 保存修改", use_container_width=True,
                     type="primary", key="dialog_save"):
            update_record(edit_id, {
                "amount": new_amount,
                "type": new_type,
                "category_main": new_main,
                "category_sub": new_sub,
                "note": new_note if new_note else "",
                "timestamp": datetime.combine(new_date, new_time)
            })
            st.session_state.editing_id = None
            st.rerun()
    with btn_col2:
        if st.button("↩️ 取消", use_container_width=True, key="dialog_cancel"):
            st.session_state.editing_id = None
            st.rerun()




# ============================================================
# 侧边栏：导航 + 全局汇总
# ============================================================
with st.sidebar:
    st.title("🧾 AI记账")
    st.caption("像聊天一样记账")

    st.divider()

    # 导航菜单
    page = st.radio(
        "",
        ["🧾 记账", "📋 历史记账", "📊 记账统计"],
        label_visibility="collapsed"
    )

    st.divider()

    # 全局汇总卡片（无论在哪个页面都可见）
    records = st.session_state.records
    if len(records) > 0:
        now = now_cn()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=now.weekday())

        today_records = records[pd.to_datetime(records["timestamp"]) >= today_start]
        week_records = records[pd.to_datetime(records["timestamp"]) >= week_start]

        today_expense = today_records[today_records["type"] == "支出"]["amount"].sum()
        today_income = today_records[today_records["type"] == "收入"]["amount"].sum()
        week_expense = week_records[week_records["type"] == "支出"]["amount"].sum()

        st.caption("📅 今日支出")
        st.markdown(f"#### ¥{today_expense:.2f}")
        st.caption("💰 今日收入")
        st.markdown(f"#### ¥{today_income:.2f}")
        st.caption("📊 本周支出")
        st.markdown(f"#### ¥{week_expense:.2f}")
        st.caption(f"📝 共 {len(records)} 条记录")
    else:
        st.caption("📝 暂无记录，去记一笔吧！")


# ============================================================
# 辅助函数：获取筛选后的记录
# ============================================================
def get_filtered_records(records_df, start_date=None, end_date=None,
                         record_type="全部", main_category="全部", search_text=""):
    """根据筛选条件过滤记录"""
    df = records_df.copy()
    if len(df) == 0:
        return df

    df["timestamp_dt"] = pd.to_datetime(df["timestamp"])

    # 日期范围筛选
    if start_date:
        df = df[df["timestamp_dt"].dt.date >= start_date]
    if end_date:
        df = df[df["timestamp_dt"].dt.date <= end_date]

    # 类型筛选
    if record_type != "全部":
        df = df[df["type"] == record_type]

    # 分类筛选
    if main_category != "全部":
        df = df[df["category_main"] == main_category]

    # 备注搜索
    if search_text:
        df = df[df["note"].str.contains(search_text, case=False, na=False)]

    return df


# ============================================================
# 页面一：🧾 记账
# ============================================================
if page == "🧾 记账":
    now = now_cn()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=now.weekday())

    # 顶部汇总卡片
    if len(records) > 0:
        today_records = records[pd.to_datetime(records["timestamp"]) >= today_start]
        week_records = records[pd.to_datetime(records["timestamp"]) >= week_start]

        today_expense = today_records[today_records["type"] == "支出"]["amount"].sum()
        today_income = today_records[today_records["type"] == "收入"]["amount"].sum()
        week_expense = week_records[week_records["type"] == "支出"]["amount"].sum()

        col1, col2, col3 = st.columns(3)
        with col1:
            with st.container(border=True):
                st.caption("📅 今日支出")
                st.markdown(f"#### ¥{today_expense:.2f}")
        with col2:
            with st.container(border=True):
                st.caption("💰 今日收入")
                st.markdown(f"#### ¥{today_income:.2f}")
        with col3:
            with st.container(border=True):
                st.caption("📊 本周支出")
                st.markdown(f"#### ¥{week_expense:.2f}")

    st.divider()

    # ---- 编辑弹窗 ----
    if st.session_state.editing_id:
        edit_id = st.session_state.editing_id
        edit_record = records[records["id"] == edit_id]
        if len(edit_record) == 0:
            st.session_state.editing_id = None
            st.rerun()
        st.session_state._edit_dialog_rendered = False
        edit_dialog(edit_record.iloc[0], edit_id)
        if not st.session_state._edit_dialog_rendered:
            # 弹窗被 ✕ 关闭，函数体未执行 → 清空状态，防止重复弹出
            st.session_state.editing_id = None
            st.rerun()

    # ---- 聊天式记账（微信风格：用户右，AI 左） ----
    today_start_dt = datetime.combine(now_cn().date(), datetime.min.time())
    today_records_list = records[pd.to_datetime(records["timestamp"]) >= today_start_dt]

    if len(st.session_state.chat_history) > 0:
        if len(today_records_list) > 0:
            today_total = today_records_list[today_records_list["type"] == "支出"]["amount"].sum()
            st.caption(f"📋 今日 · {len(today_records_list)}笔 · 支出 ¥{today_total:.2f}")

        for ch in st.session_state.chat_history:
            rec_id = ch["record_id"]
            rec_match = records[records["id"] == rec_id]
            if len(rec_match) == 0:
                continue
            row = rec_match.iloc[0]
            r_ts = pd.to_datetime(row["timestamp"])
            sign = "+" if row["type"] == "收入" else "-"

            # 用户消息行（头像在右，气泡靠右）
            _, msg_col, avatar_col = st.columns([3.3, 1.95, 0.25])
            with msg_col:
                st.html(f"""<div style="text-align:right;">
                    <span style="display:inline-block;max-width:100%;text-align:left;
                        background:#d4e6f1;border-radius:12px;padding:8px 14px;
                        font-size:14px;color:#1a1a1a;word-break:break-word;line-height:1.5;
                        border:1px solid #b8d4e3;">
                        {ch["user_text"]}
                    </span>
                </div>""")
            with avatar_col:
                st.html("<div style='display:flex;justify-content:flex-end;'><div style='width:40px;height:40px;border-radius:50%;background:#07C160;color:#fff;display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:bold;'>我</div></div>")

            # AI 记账卡片行（头像在左，卡片靠左，间距对齐）
            avatar_col2, card_col, _ = st.columns([0.25, 1.95, 3.3], gap="small")
            with avatar_col2:
                st.html("<div style='width:40px;height:40px;border-radius:50%;background:#1f77b4;color:#fff;display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:bold;'>AI</div>")
            with card_col:
                with st.container(border=True):
                    st.caption(
                        f"{sign}¥{float(row['amount']):.2f}  {row['category_sub']}  "
                        f"🕐 {r_ts.strftime('%m月%d日 %H:%M')}"
                    )
                    note_text = row.get("note", "")
                    if note_text:
                        st.caption(f"📝 {note_text}")
                    btn_l, btn_r, _ = st.columns([1.5, 1.5, 3], gap="small")
                    with btn_l:
                        if st.button("✏️\n编辑", key=f"chat_edit_{rec_id}", use_container_width=True):
                            st.session_state.editing_id = rec_id
                            st.rerun()
                    with btn_r:
                        if st.button("🗑️\n删除", key=f"chat_del_{rec_id}", use_container_width=True):
                            delete_record(rec_id)
                            st.session_state.chat_history = [
                                c for c in st.session_state.chat_history
                                if c["record_id"] != rec_id
                            ]
                            if st.session_state.last_added_id == rec_id:
                                st.session_state.last_added_id = None
                            _save_json()
                            st.rerun()
    else:
        if len(records) == 0:
            st.info("👋 欢迎使用AI记账！在下方输入框说一句话，AI帮你自动分类归档。")
            st.caption("💡 试试说：午饭食堂35块 / 打车26.5 / 超市买了牛奶45")
        elif len(today_records_list) > 0:
            today_total = today_records_list[today_records_list["type"] == "支出"]["amount"].sum()
            st.info(f"📋 已加载 {len(records)} 条记录 · 今日 {len(today_records_list)} 笔 · 支出 ¥{today_total:.2f}")
        else:
            st.info(f"📋 已加载 {len(records)} 条记录，今天还没有新记录")

    # ---- 聊天输入框 ----
    user_input = st.chat_input("说说你今天花了什么钱...（如：午饭食堂35块）")

    if user_input:
        if "DEEPSEEK_API_KEY" not in st.secrets:
            st.error("🔑 请先配置 DeepSeek API Key")
            st.caption(
                "在 Streamlit Cloud 设置中添加 Secret：`DEEPSEEK_API_KEY`\n"
                "本地运行时创建 `.streamlit/secrets.toml` 文件"
            )
        else:
            # 用户消息（右侧气泡，跟聊天历史统一布局）
            _, msg_col, ava_col = st.columns([3.3, 1.95, 0.25])
            with msg_col:
                st.html(f"""<div style="text-align:right;">
                    <span style="display:inline-block;max-width:100%;text-align:left;
                        background:#d4e6f1;border-radius:12px;padding:8px 14px;
                        font-size:14px;color:#1a1a1a;word-break:break-word;line-height:1.5;
                        border:1px solid #b8d4e3;">
                        {user_input}
                    </span>
                </div>""")
            with ava_col:
                st.html("<div style='display:flex;justify-content:flex-end;'><div style='width:40px;height:40px;border-radius:50%;background:#07C160;color:#fff;display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:bold;'>我</div></div>")

            # AI 处理中 / 返回结果（左侧，间距对齐）
            ava2, res_col, _ = st.columns([0.25, 1.95, 3.3], gap="small")
            with ava2:
                st.html("<div style='width:40px;height:40px;border-radius:50%;background:#1f77b4;color:#fff;display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:bold;'>AI</div>")
            with res_col:
                with st.spinner("🤔 AI 正在理解你的消费..."):
                    parsed = parse_input(user_input)

                if parsed is None:
                    err_type = st.session_state.get("parse_error")
                    if err_type == "no_amount":
                        st.warning("😅 没找到具体金额，这笔就不记了")
                        st.caption("💡 请带上金额重新输入，比如：午饭35块 / 打车26.5")
                    else:
                        st.warning("😅 没太理解这笔消费，不记入账单")
                        st.caption("💡 换个说法试试：午饭35块 / 打车26.5 / 超市买了牛奶45")
                    st.session_state.parse_error = None
                else:
                    add_record(parsed)
                    new_id = f"rec_{st.session_state.record_counter:04d}"
                    st.session_state.chat_history.append({
                        "user_text": user_input,
                        "record_id": new_id,
                    })
                    _save_json()
                    st.rerun()

# ============================================================
# 页面二：📋 历史记账
# ============================================================
elif page == "📋 历史记账":
    st.header("📋 历史记账")

    if len(records) == 0:
        st.info("👋 还没有记账记录，去「记账」页开始记第一笔吧！")
    else:
        # ---- 筛选栏 ----
        with st.container(border=True):
            st.caption("🔍 筛选条件")
            filter_col1, filter_col2, filter_col3 = st.columns(3)

            with filter_col1:
                # 日期范围
                min_date = pd.to_datetime(records["timestamp"]).min().date()
                max_date = pd.to_datetime(records["timestamp"]).max().date()
                date_range = st.date_input(
                    "📅 日期范围",
                    value=(min_date, max_date),
                    min_value=min_date,
                    max_value=max_date
                )
                if len(date_range) == 2:
                    start_date, end_date = date_range[0], date_range[1]
                else:
                    start_date, end_date = None, None

            with filter_col2:
                # 类型筛选
                record_type = st.selectbox(
                    "📌 类型",
                    ["全部", "支出", "收入"]
                )
                # 一级分类筛选
                main_category = st.selectbox(
                    "📂 一级分类",
                    ["全部"] + list(CATEGORY_MAP.keys())
                )

            with filter_col3:
                # 备注搜索
                search_text = st.text_input(
                    "🔍 搜索备注",
                    placeholder="输入关键词..."
                )

        # ---- 编辑弹窗 ----
        if st.session_state.editing_id:
            edit_id = st.session_state.editing_id
            edit_record = records[records["id"] == edit_id]
            if len(edit_record) == 0:
                st.session_state.editing_id = None
                st.rerun()
            st.session_state._edit_dialog_rendered = False
            edit_dialog(edit_record.iloc[0], edit_id)
            if not st.session_state._edit_dialog_rendered:
                # 弹窗被 ✕ 关闭，函数体未执行 → 清空状态，防止重复弹出
                st.session_state.editing_id = None
                st.rerun()

        # ---- 删除确认弹窗 ----
        if st.session_state.confirm_delete_id:
            target_id = st.session_state.confirm_delete_id
            target_record = records[records["id"] == target_id]
            if len(target_record) > 0:
                target = target_record.iloc[0]
                with st.container(border=True):
                    st.warning(f"⚠️ 确定要删除这条记录吗？")
                    st.caption(
                        f"{target['category_sub']} | "
                        f"¥{target['amount']:.2f} | "
                        f"{target['timestamp'].strftime('%m月%d日 %H:%M')} | "
                        f"{target['note']}"
                    )
                    del_col1, del_col2, _ = st.columns([1, 1, 4])
                    with del_col1:
                        if st.button("✅ 确认删除", key="confirm_del_btn", type="primary"):
                            delete_record(target_id)
                            st.session_state.confirm_delete_id = None
                            st.rerun()
                    with del_col2:
                        if st.button("❌ 取消", key="cancel_del_btn"):
                            st.session_state.confirm_delete_id = None
                            st.rerun()

        # ---- 筛选数据（编辑和删除确认时不显示列表） ----
        if not st.session_state.editing_id and not st.session_state.confirm_delete_id:
            filtered_df = get_filtered_records(
                records, start_date, end_date, record_type, main_category, search_text
            )

            st.divider()

            # ---- 操作栏：撤销 + 记录数 ----
            op_col1, op_col2, op_col3 = st.columns([1, 1, 4])
            with op_col1:
                st.button("↩️ 撤销上一条", on_click=undo_last, use_container_width=True)
            with op_col2:
                st.caption(f"显示 {len(filtered_df)} / {len(records)} 条")

            # ---- 记录列表 ----
            if len(filtered_df) == 0:
                st.info("没有匹配的记录，试试调整筛选条件。")
            else:
                display_df = filtered_df.sort_values("timestamp", ascending=False).copy()
                display_df["时间"] = pd.to_datetime(display_df["timestamp"]).apply(
                    lambda t: t.strftime("%m/%d %H:%M")
                )
                display_df["金额"] = display_df.apply(
                    lambda row: f"{'收入' if row['type'] == '收入' else '支出'} ¥{row['amount']:.2f}",
                    axis=1
                )

                # 用 columns 布局展示每条记录 + 编辑/删除按钮
                for _, row in display_df.iterrows():
                    with st.container(border=True):
                        r_col1, r_col2, r_col3, r_col4, r_col5 = st.columns([1.5, 1, 1.2, 0.5, 0.5])
                        with r_col1:
                            st.caption(f"{row['金额']}")
                        with r_col2:
                            st.caption(f"{row['category_sub']}")
                        with r_col3:
                            note_display = row['note'] if row['note'] else "(无备注)"
                            st.caption(f"📝 {note_display} | {row['时间']}")
                        with r_col4:
                            if st.button("✏️", key=f"edit_{row['id']}", help="编辑此条记录"):
                                st.session_state.editing_id = row['id']
                                st.rerun()
                        with r_col5:
                            if st.button("🗑️", key=f"del_{row['id']}", help="删除此条记录"):
                                st.session_state.confirm_delete_id = row['id']
                                st.rerun()

# ============================================================
# 页面三：📊 记账统计
# ============================================================
elif page == "📊 记账统计":
    st.header("📊 记账统计")

    if len(records) == 0:
        st.info("👋 还没有记账记录，去「记账」页开始记第一笔吧！有了数据才能统计哦。")
    else:
        now = now_cn()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=now.weekday())
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        days30_start = today_start - timedelta(days=29)

        # ---- 时间段选择 ----
        period = st.radio(
            "⏰ 统计时间段",
            ["📅 本周", "📅 本月", "📅 近30天", "📅 全部"],
            horizontal=True
        )

        # 确定时间范围
        if period == "📅 本周":
            period_start = week_start
            period_label = f"{week_start.strftime('%m/%d')} - {now.strftime('%m/%d')}"
        elif period == "📅 本月":
            period_start = month_start
            period_label = f"{month_start.strftime('%m/%d')} - {now.strftime('%m/%d')}"
        elif period == "📅 近30天":
            period_start = days30_start
            period_label = f"{days30_start.strftime('%m/%d')} - {now.strftime('%m/%d')}"
        else:  # 全部
            period_start = pd.to_datetime(records["timestamp"]).min()
            period_label = f"{period_start.strftime('%Y/%m/%d')} - {now.strftime('%m/%d')}"

        # 筛选时间段内的记录
        period_records = records[pd.to_datetime(records["timestamp"]) >= period_start]
        period_expense = period_records[period_records["type"] == "支出"]
        period_income = period_records[period_records["type"] == "收入"]

        total_expense = period_expense["amount"].sum()
        total_income = period_income["amount"].sum()
        balance = total_income - total_expense
        record_count = len(period_records)
        avg_daily = total_expense / max((now - period_start).days, 1) if period != "📅 全部" else total_expense / max((now - pd.to_datetime(records["timestamp"]).min()).days, 1)

        st.caption(f"📅 {period_label}")

        # ---- 汇总卡片 ----
        card1, card2, card3, card4 = st.columns(4)
        with card1:
            with st.container(border=True):
                st.caption("📅 总支出")
                st.markdown(f"#### ¥{total_expense:.2f}")
        with card2:
            with st.container(border=True):
                st.caption("💰 总收入")
                st.markdown(f"#### ¥{total_income:.2f}")
        with card3:
            with st.container(border=True):
                st.caption("💎 结余")
                balance_sign = "+" if balance >= 0 else ""
                st.markdown(f"#### {balance_sign}¥{balance:.2f}")
        with card4:
            with st.container(border=True):
                st.caption("📊 日均支出")
                st.markdown(f"#### ¥{avg_daily:.2f}")

        st.divider()

        # ---- 图表区 ----
        chart_col1, chart_col2 = st.columns(2)

        # Plotly 通用配置：禁用缩放/平移
        chart_config = {
            "displayModeBar": False,
            "staticPlot": False,
            "scrollZoom": False,
            "doubleClick": False,
            "showTips": False,
        }

        with chart_col1:
            # 分类支出饼图（随时间段切换变化）
            st.subheader(f"📂 分类支出分布 · {period_label}")
            if len(period_expense) > 0:
                cat_data = period_expense.groupby("category_main")["amount"].sum()
                fig_pie = go.Figure(data=[
                    go.Pie(
                        labels=cat_data.index.tolist(),
                        values=cat_data.values.tolist(),
                        hole=0.4,
                        textinfo="percent",
                        textposition="inside",
                        textfont=dict(size=12),
                        marker=dict(line=dict(color="white", width=1)),
                    )
                ])
                fig_pie.update_layout(
                    margin=dict(t=10, b=40, l=10, r=10),
                    height=320,
                    showlegend=True,
                    legend=dict(
                        orientation="h", y=-0.08, x=0.5, xanchor="center",
                        font=dict(size=11)
                    ),
                )
                st.plotly_chart(fig_pie, use_container_width=True, config=chart_config, key=f"pie_{period}")
            else:
                st.info("该时间段无支出记录")

        with chart_col2:
            # 每日支出趋势折线图
            st.subheader("📈 每日支出趋势")
            if len(period_expense) > 0:
                daily_data = period_expense.copy()
                daily_data["date"] = pd.to_datetime(daily_data["timestamp"]).dt.date
                daily_trend = daily_data.groupby("date")["amount"].sum()
                # 填充没有记录的日期为0
                if period != "📅 全部":
                    all_dates = pd.date_range(period_start.date(), now.date(), freq="D")
                    daily_trend = daily_trend.reindex(all_dates.date, fill_value=0)
                fig_line = go.Figure(data=[
                    go.Scatter(
                        x=[d.strftime("%m/%d") for d in daily_trend.index],
                        y=daily_trend.values.tolist(),
                        mode="lines+markers",
                        line=dict(color="#1f77b4", width=2),
                        marker=dict(size=4),
                        fill="tozeroy",
                        fillcolor="rgba(31,119,180,0.1)",
                    )
                ])
                fig_line.update_layout(
                    margin=dict(t=10, b=10, l=10, r=10),
                    height=350,
                    xaxis=dict(title=None),
                    yaxis=dict(title=None),
                )
                st.plotly_chart(fig_line, use_container_width=True, config=chart_config)
            else:
                st.info("该时间段无支出记录")

        # ---- 收支对比 ----
        if len(period_income) > 0:
            st.divider()
            st.subheader("⚖️ 收支对比")
            compare_data = pd.DataFrame({
                "支出": [total_expense],
                "收入": [total_income]
            })
            st.bar_chart(compare_data, use_container_width=True)

        # ---- 二级分类明细 ----
        if len(period_expense) > 0:
            st.divider()
            st.subheader("🔍 二级分类明细")
            sub_data = period_expense.groupby(["category_main", "category_sub"])["amount"].sum()
            sub_data = sub_data.sort_values(ascending=False)

            # 转为可展示的格式
            sub_rows = []
            for (main_cat, sub_cat), amt in sub_data.items():
                sub_rows.append({
                    "一级分类": main_cat,
                    "二级分类": sub_cat,
                    "金额": f"¥{amt:.2f}",
                    "占比": f"{amt/total_expense*100:.1f}%" if total_expense > 0 else "0%"
                })
            sub_df = pd.DataFrame(sub_rows)
            st.dataframe(sub_df, use_container_width=True, hide_index=True)

        # ---- AI 洞察周报 ----
        st.divider()
        st.subheader("🤖 AI 洞察周报")
        st.caption("基于本周数据，AI生成幽默风格的消费洞察报告")

        if st.button("🤖 生成AI洞察周报", type="primary", use_container_width=True):
            # 获取本周数据
            week_records_all = records[pd.to_datetime(records["timestamp"]) >= week_start]
            week_expense_all = week_records_all[week_records_all["type"] == "支出"]["amount"].sum()
            week_income_all = week_records_all[week_records_all["type"] == "收入"]["amount"].sum()

            if len(week_records_all) == 0:
                st.warning("本周还没有记录，先去记几笔吧！")
            elif "DEEPSEEK_API_KEY" not in st.secrets:
                st.error("🔑 请先配置 DeepSeek API Key 才能生成周报")
            else:
                with st.spinner("🤖 AI正在分析你的消费习惯..."):
                    try:
                        # 准备本周数据摘要
                        week_summary = []
                        for _, rec in week_records_all.iterrows():
                            week_summary.append(
                                f"- {rec['category_sub']} ¥{rec['amount']:.2f} "
                                f"({rec['timestamp'].strftime('%m/%d %H:%M')}) "
                                f"{rec['note'] if rec['note'] else ''}"
                            )

                        week_text = "\n".join(week_summary)
                        cat_ranking = week_records_all[week_records_all["type"]=="支出"].groupby("category_main")["amount"].sum().sort_values(ascending=False)

                        client = get_client()
                        report_response = client.chat.completions.create(
                            model="deepseek-chat",
                            messages=[
                                {
                                    "role": "system",
                                    "content": (
                                        "你是一个幽默风趣的财务管家。根据用户的消费数据，写一段200字以内的周报总结。"
                                        "风格要求：轻松幽默、有网感、像朋友在聊天。"
                                        "内容要点：本周总支出、花钱最多的类别、一个省钱建议。"
                                        "用Markdown格式，加几个emoji。"
                                    )
                                },
                                {
                                    "role": "user",
                                    "content": (
                                        f"本周（{week_start.strftime('%m/%d')}-{now.strftime('%m/%d')}）消费记录：\n"
                                        f"总支出：¥{week_expense_all:.2f}\n"
                                        f"总收入：¥{week_income_all:.2f}\n"
                                        f"共{len(week_records_all)}笔\n\n"
                                        f"分类排行：\n{cat_ranking.to_string()}\n\n"
                                        f"明细：\n{week_text}"
                                    )
                                }
                            ],
                            temperature=0.8,
                            max_tokens=400
                        )
                        report = report_response.choices[0].message.content.strip()

                        with st.container(border=True):
                            st.markdown(report)
                    except Exception:
                        st.error("生成周报失败，请稍后再试。")

# ============================================================
# 页脚
# ============================================================
st.sidebar.divider()
st.sidebar.caption("🚧 V1.1 | Powered by DeepSeek + Streamlit")
