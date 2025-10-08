import streamlit as st
import pandas as pd
import numpy as np
import json
import io
from docx import Document
from google import genai
from google.genai.errors import APIError

# --- Cấu hình Trang Streamlit ---
st.set_page_config(
    page_title="App Thẩm Định Dự Án Đầu Tư (NPV, IRR)",
    layout="wide"
)

st.title("Ứng dụng Thẩm Định Dự Án Kinh doanh 📊")

# Khởi tạo trạng thái phiên (Session State)
if 'extracted_data' not in st.session_state:
    st.session_state['extracted_data'] = None

# --- Khai báo API Key ---
API_KEY = st.secrets.get("GEMINI_API_KEY")

# --- Hàm đọc nội dung từ file Word ---
def read_docx(uploaded_file):
    """Đọc toàn bộ văn bản từ file Word (.docx) đã tải lên."""
    try:
        # Sử dụng io.BytesIO để xử lý file trong bộ nhớ
        doc = Document(io.BytesIO(uploaded_file.read()))
        full_text = []
        for para in doc.paragraphs:
            full_text.append(para.text)
        return '\n'.join(full_text)
    except Exception as e:
        st.error(f"Lỗi khi đọc file Word: {e}")
        return None

# --- Hàm gọi AI để Trích xuất Dữ liệu (Nhiệm vụ 1) ---
def extract_financial_data(doc_content, api_key):
    """Sử dụng Gemini API với JSON Schema để trích xuất các chỉ số tài chính."""
    if not api_key:
        st.error("Lỗi: Không tìm thấy Khóa API. Vui lòng kiểm tra cấu hình Streamlit Secrets.")
        return None

    try:
        client = genai.Client(api_key=api_key)
        model_name = 'gemini-2.5-flash'

        system_prompt = (
            "Bạn là một chuyên gia phân tích dữ liệu tài chính. Nhiệm vụ của bạn là trích xuất chính xác 6 chỉ số sau từ tài liệu kinh doanh "
            "do người dùng cung cấp và định dạng chúng thành một đối tượng JSON. Nếu một đơn vị tiền tệ (ví dụ: VNĐ, tỷ, triệu) được đề cập, "
            "hãy chuyển đổi tất cả các giá trị tiền tệ về cùng một đơn vị là **VNĐ** (đơn vị cơ bản). WACC và Thuế phải là tỷ lệ phần trăm (0.00 đến 1.00)."
        )

        user_query = f"""
        Trích xuất 6 chỉ số sau từ văn bản:
        1. Vốn đầu tư (I0)
        2. Dòng đời dự án (Năm)
        3. Doanh thu hàng năm (R)
        4. Chi phí hàng năm (C, không bao gồm khấu hao)
        5. WACC (%)
        6. Thuế suất (%)
        ---
        Văn bản nguồn:
        {doc_content}
        """

        # Định nghĩa JSON Schema bắt buộc
        response_schema = {
            "type": "OBJECT",
            "properties": {
                "vốn_đầu_tư": {"type": "NUMBER", "description": "Tổng vốn đầu tư ban đầu, quy đổi ra VNĐ."},
                "dòng_đời_dự_án": {"type": "INTEGER", "description": "Số năm hoạt động của dự án."},
                "doanh_thu_hàng_năm": {"type": "NUMBER", "description": "Doanh thu hoạt động hàng năm, quy đổi ra VNĐ."},
                "chi_phí_hàng_năm": {"type": "NUMBER", "description": "Tổng chi phí vận hành hàng năm, quy đổi ra VNĐ."},
                "wacc": {"type": "NUMBER", "description": "Tỷ lệ WACC của doanh nghiệp (0.00 đến 1.00)."},
                "thuế_suất": {"type": "NUMBER", "description": "Tỷ lệ thuế TNDN (0.00 đến 1.00)."}
            },
            "required": [
                "vốn_đầu_tư", "dòng_đời_dự_án", "doanh_thu_hàng_năm",
                "chi_phí_hàng_năm", "wacc", "thuế_suất"
            ]
        }

        with st.spinner('Đang gửi file Word và trích xuất dữ liệu bằng AI...'):
            response = client.models.generate_content(
                model=model_name,
                contents=user_query,
                config={
                    "systemInstruction": system_prompt,
                    "responseMimeType": "application/json",
                    "responseSchema": response_schema
                }
            )

        # Trả về JSON đã được parse
        return json.loads(response.text)

    except APIError as e:
        st.error(f"Lỗi gọi Gemini API: Vui lòng kiểm tra Khóa API hoặc giới hạn sử dụng. Chi tiết lỗi: {e}")
        return None
    except json.JSONDecodeError:
        st.error("AI không thể trả về định dạng JSON hợp lệ. Vui lòng kiểm tra lại nội dung file Word.")
        return None
    except Exception as e:
        st.error(f"Đã xảy ra lỗi không xác định trong quá trình trích xuất: {e}")
        return None

# --- Hàm tính toán Dòng tiền và các Chỉ số (Nhiệm vụ 2 & 3) ---
@st.cache_data
def calculate_project_metrics(data):
    """Tính toán bảng dòng tiền, NPV, IRR, PP, và DPP."""
    I_0 = data['vốn_đầu_tư']
    N = data['dòng_đời_dự_án']
    R = data['doanh_thu_hàng_năm']
    C = data['chi_phí_hàng_năm']
    WACC = data['wacc']
    Tax = data['thuế_suất']

    # Kiểm tra điều kiện đầu vào
    if N <= 0:
        raise ValueError("Dòng đời dự án phải lớn hơn 0.")
    if WACC <= 0:
        raise ValueError("WACC phải lớn hơn 0 để tính chiết khấu.")

    # Giả định Khấu hao: Phương pháp đường thẳng (Dep = I_0 / N)
    Depreciation = I_0 / N

    years = np.arange(0, N + 1)
    df = pd.DataFrame(index=years)
    df.index.name = 'Năm'

    # 1. Bảng Dòng tiền (Cash Flow Table)
    df.loc[0, 'Dòng tiền Thuần (CF)'] = -I_0

    for y in years[1:]:
        EBIT = R - C - Depreciation
        Tax_Amount = EBIT * Tax if EBIT > 0 else 0
        EAT = EBIT - Tax_Amount
        # CF = EAT + Dep (Giả định không có vốn lưu động thay đổi và giá trị thanh lý)
        CF = EAT + Depreciation
        df.loc[y, 'Doanh thu (R)'] = R
        df.loc[y, 'Chi phí (C)'] = C
        df.loc[y, 'Khấu hao (Dep)'] = Depreciation
        df.loc[y, 'Lợi nhuận trước Thuế & Lãi (EBIT)'] = EBIT
        df.loc[y, 'Thuế (T)'] = Tax_Amount
        df.loc[y, 'Lợi nhuận sau Thuế (EAT)'] = EAT
        df.loc[y, 'Dòng tiền Thuần (CF)'] = CF
        
    # Tính Dòng tiền chiết khấu (DCF)
    df['Hệ số Chiết khấu'] = 1 / ((1 + WACC) ** df.index)
    df.loc[0, 'Hệ số Chiết khấu'] = 1.0 # Hệ số chiết khấu năm 0 là 1
    df['Dòng tiền Chiết khấu (DCF)'] = df['Dòng tiền Thuần (CF)'] * df['Hệ số Chiết khấu']


    # 2. Tính toán Chỉ số (Metrics)
    CF_array = df['Dòng tiền Thuần (CF)'].values
    
    # NPV (Net Present Value)
    NPV = df['Dòng tiền Chiết khấu (DCF)'].sum() 

    # IRR (Internal Rate of Return)
    try:
        IRR = np.irr(CF_array)
    except Exception:
        IRR = np.nan

    # Payback Period (PP)
    cumulative_cf = np.cumsum(CF_array)
    PP = N + 1 # Mặc định là không hoàn vốn trong vòng đời dự án
    for i in range(1, len(cumulative_cf)):
        if cumulative_cf[i] >= 0:
            # Nội suy tuyến tính
            PP = (i - 1) + (-cumulative_cf[i - 1] / CF_array[i])
            break

    # Discounted Payback Period (DPP)
    cumulative_dcf = np.cumsum(df['Dòng tiền Chiết khấu (DCF)'].values)
    DPP = N + 1 # Mặc định là không hoàn vốn
    for i in range(1, len(cumulative_dcf)):
        if cumulative_dcf[i] >= 0:
            # Nội suy tuyến tính
            DPP = (i - 1) + (-cumulative_dcf[i - 1] / df.loc[i, 'Dòng tiền Chiết khấu (DCF)'])
            break
    
    # Lưu kết quả
    metrics = {
        'NPV': NPV,
        'IRR': IRR,
        'PP': PP,
        'DPP': DPP,
        'WACC': WACC
    }
    
    return df, metrics

# --- Hàm gọi AI để Phân tích Chỉ số (Nhiệm vụ 4) ---
def get_ai_analysis_metrics(metrics, api_key):
    """Yêu cầu Gemini AI phân tích các chỉ số NPV, IRR, PP, DPP."""
    try:
        client = genai.Client(api_key=api_key)
        model_name = 'gemini-2.5-flash'

        # Đảm bảo IRR không phải là NaN
        irr_display = f"{metrics['IRR'] * 100:.2f}%" if not np.isnan(metrics['IRR']) else "Không xác định (dòng tiền âm kéo dài)"
        
        prompt = f"""
        Bạn là một chuyên gia thẩm định dự án đầu tư có kinh nghiệm. Dựa trên các chỉ số hiệu quả kinh tế sau của một dự án, hãy đưa ra một đánh giá chuyên nghiệp và kết luận (khoảng 3 đoạn) về tính khả thi của dự án.
        
        Các chỉ số:
        - NPV (Giá trị hiện tại ròng): {metrics['NPV']:,.0f} VNĐ
        - IRR (Tỷ suất sinh lợi nội tại): {irr_display}
        - WACC (Chi phí vốn bình quân): {metrics['WACC'] * 100:.2f}%
        - PP (Thời gian hoàn vốn): {metrics['PP']:.2f} năm
        - DPP (Thời gian hoàn vốn có chiết khấu): {metrics['DPP']:.2f} năm
        
        Dòng đời dự án là {st.session_state['extracted_data']['dòng_đời_dự_án']} năm.

        Nội dung phân tích cần tập trung vào:
        1. Nhận xét về NPV: Dự án có tạo ra giá trị kinh tế dương cho doanh nghiệp không?
        2. So sánh IRR và WACC: Dự án có nên được chấp nhận đầu tư theo tiêu chí IRR không?
        3. Đánh giá thời gian hoàn vốn (PP và DPP) so với dòng đời dự án: Thời gian hoàn vốn có nằm trong giới hạn chấp nhận không?
        4. Đưa ra kết luận cuối cùng về tính hiệu quả tài chính của dự án.
        """

        with st.spinner('Đang gửi các chỉ số và chờ Gemini phân tích...'):
            response = client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            return response.text

    except APIError as e:
        return f"Lỗi gọi Gemini API: Vui lòng kiểm tra Khóa API hoặc giới hạn sử dụng. Chi tiết lỗi: {e}"
    except Exception as e:
        return f"Đã xảy ra lỗi không xác định trong quá trình phân tích: {e}"

# ----------------------------------------------------
# --- Logic Chính của Ứng dụng Streamlit ---
# ----------------------------------------------------

# --- Nhiệm vụ 1: Tải File Word và Trích xuất Dữ liệu ---
st.subheader("1. Tải File Word và Trích xuất Dữ liệu")
uploaded_file = st.file_uploader(
    "Vui lòng tải lên file Word (.docx) chứa Phương án Đầu tư:",
    type=['docx']
)

if uploaded_file is not None:
    # Đọc nội dung file Word
    doc_content = read_docx(uploaded_file)
    
    # Nút bấm để thực hiện trích xuất dữ liệu
    if st.button("🔴 Lọc Dữ liệu Tài chính bằng AI", type="primary"):
        if doc_content:
            st.session_state['extracted_data'] = extract_financial_data(doc_content, API_KEY)
            
            # Xóa các kết quả tính toán cũ nếu có dữ liệu mới
            st.session_state['df_cf'] = None
            st.session_state['metrics'] = None

    if st.session_state['extracted_data']:
        st.success("✅ Trích xuất dữ liệu thành công!")
        data = st.session_state['extracted_data']
        
        st.markdown("#### Các tham số đã trích xuất:")
        
        col_list = st.columns(3)
        col_list[0].metric("Vốn Đầu tư (I₀)", f"{data['vốn_đầu_tư']:,.0f} VNĐ")
        col_list[1].metric("Dòng đời Dự án (N)", f"{data['dòng_đời_dự_án']} năm")
        col_list[2].metric("WACC", f"{data['wacc'] * 100:.2f}%")
        
        col_list2 = st.columns(3)
        col_list2[0].metric("Doanh thu hàng năm (R)", f"{data['doanh_thu_hàng_năm']:,.0f} VNĐ")
        col_list2[1].metric("Chi phí hàng năm (C)", f"{data['chi_phí_hàng_năm']:,.0f} VNĐ")
        col_list2[2].metric("Thuế suất (T)", f"{data['thuế_suất'] * 100:.2f}%")
        
        st.divider()

        # --- Nhiệm vụ 2 & 3: Xây dựng Bảng Dòng tiền và Tính toán Chỉ số ---
        st.subheader("2. Bảng Dòng tiền và 3. Chỉ số Hiệu quả Dự án")
        
        try:
            # Thực hiện tính toán
            df_cf, metrics = calculate_project_metrics(data)
            st.session_state['df_cf'] = df_cf
            st.session_state['metrics'] = metrics
            
            # Hiển thị bảng dòng tiền
            st.markdown("#### Bảng Dòng tiền Chiết khấu (DCF)")
            st.dataframe(df_cf.style.format({
                'Doanh thu (R)': '{:,.0f}',
                'Chi phí (C)': '{:,.0f}',
                'Khấu hao (Dep)': '{:,.0f}',
                'Lợi nhuận trước Thuế & Lãi (EBIT)': '{:,.0f}',
                'Thuế (T)': '{:,.0f}',
                'Lợi nhuận sau Thuế (EAT)': '{:,.0f}',
                'Dòng tiền Thuần (CF)': '{:,.0f}',
                'Dòng tiền Chiết khấu (DCF)': '{:,.0f}',
                'Hệ số Chiết khấu': '{:.4f}',
            }), use_container_width=True)
            
            # Hiển thị các chỉ số
            st.markdown("#### Kết quả Thẩm định")
            metrics_cols = st.columns(4)
            
            # Hiển thị NPV
            npv_value = metrics['NPV']
            npv_delta = "Dự án tạo ra giá trị" if npv_value > 0 else "Dự án không tạo ra giá trị"
            metrics_cols[0].metric("NPV (Giá trị hiện tại ròng)", f"{npv_value:,.0f} VNĐ", delta=npv_delta)

            # Hiển thị IRR
            irr_value = metrics['IRR']
            irr_text = f"{irr_value * 100:.2f}%" if not np.isnan(irr_value) else "N/A"
            irr_delta = "Chấp nhận" if not np.isnan(irr_value) and irr_value > data['wacc'] else "Xem xét"
            metrics_cols[1].metric("IRR (Tỷ suất sinh lợi nội tại)", irr_text, delta=irr_delta)

            # Hiển thị PP
            pp_value = metrics['PP']
            pp_delta = f"({data['dòng_đời_dự_án']} năm)"
            metrics_cols[2].metric("PP (Thời gian hoàn vốn)", f"{pp_value:.2f} năm", delta=pp_delta)
            
            # Hiển thị DPP
            dpp_value = metrics['DPP']
            metrics_cols[3].metric("DPP (Hoàn vốn chiết khấu)", f"{dpp_value:.2f} năm")

            st.divider()

            # --- Nhiệm vụ 4: Yêu cầu AI Phân tích Chỉ số ---
            st.subheader("4. Phân tích Chỉ số Hiệu quả (AI)")
            if st.button("🔍 Yêu cầu AI Phân tích Kết quả Thẩm định"):
                if API_KEY:
                    ai_analysis_result = get_ai_analysis_metrics(metrics, API_KEY)
                    st.markdown("**Kết quả Phân tích từ Gemini AI:**")
                    st.info(ai_analysis_result)
                else:
                    st.error("Vui lòng cấu hình Khóa API 'GEMINI_API_KEY' trong Streamlit Secrets.")

        except ValueError as ve:
            st.error(f"Lỗi tính toán: {ve}. Vui lòng kiểm tra lại dữ liệu trích xuất.")
        except Exception as e:
            st.error(f"Lỗi không xác định trong quá trình tính toán: {e}")

else:
    st.info("Vui lòng tải lên file Word Phương án Đầu tư để bắt đầu phân tích. (Định dạng file: .docx)")
