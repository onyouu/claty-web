import streamlit as st
import requests
import datetime
import json
import google.generativeai as genai
from datetime import datetime

# ==========================================
# 1. 설정 및 API 키 로드 (Secrets 관리 권장)
# ==========================================
st.set_page_config(page_title="Claty - AI 인사이트 엔진", page_icon="🔍", layout="wide")

# Streamlit Cloud 배포 시 st.secrets에 저장하거나, 로컬 테스트 시 직접 입력
# 보안을 위해 실제 키는 별도 파일이나 환경변수로 관리하세요.
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY_HERE") 
SEARCH_API_KEY = st.secrets.get("SEARCH_API_KEY", "YOUR_SEARCH_API_KEY_HERE")
SEARCH_ENGINE_ID = st.secrets.get("SEARCH_ENGINE_ID", "YOUR_SEARCH_ENGINE_ID_HERE")
UNSPLASH_ACCESS_KEY = st.secrets.get("UNSPLASH_ACCESS_KEY", "YOUR_UNSPLASH_KEY_HERE")

genai.configure(api_key=GEMINI_API_KEY)

# ==========================================
# 2. 유틸리티 함수 (날짜, 날씨 등)
# ==========================================
def get_date_context():
    now = datetime.now()
    days = ['월', '화', '수', '목', '금', '토', '일']
    season = '겨울'
    if 3 <= now.month <= 5: season = '봄'
    elif 6 <= now.month <= 8: season = '여름'
    elif 9 <= now.month <= 11: season = '가을'
    
    return {
        "dateStr": f"{now.year}년 {now.month}월 {now.day}일 ({days[now.weekday()]})",
        "season": season,
        "year": now.year,
        "month": now.month
    }

@st.cache_data(ttl=3600) # 1시간 캐싱
def get_current_weather():
    try:
        url = 'https://api.open-meteo.com/v1/forecast?latitude=37.5665&longitude=126.9780&current=temperature_2m,weather_code&timezone=Asia%2FSeoul'
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            temp = round(data['current']['temperature_2m'])
            # 간단한 매핑
            code = data['current']['weather_code']
            condition = "맑음"
            if code in [1, 2, 3]: condition = "구름 조금"
            elif code in [45, 48]: condition = "안개"
            elif code >= 51: condition = "비 또는 눈"
            return f"{temp}°C, {condition}"
    except:
        pass
    return "정보 없음"

# ==========================================
# 3. 핵심 로직: Google 검색 + Gemini (RAG)
# ==========================================
def get_google_search_context(query):
    if not SEARCH_API_KEY or not SEARCH_ENGINE_ID:
        return None
    try:
        url = f"https://www.googleapis.com/customsearch/v1?key={SEARCH_API_KEY}&cx={SEARCH_ENGINE_ID}&q={query}&num=3"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            items = res.json().get('items', [])
            context = "\n\n".join([f"{item['title']}: {item['snippet']}" for item in items])
            return context
    except Exception as e:
        st.error(f"검색 API 오류: {e}")
    return None

def get_unsplash_image(query):
    if not UNSPLASH_ACCESS_KEY: return None
    try:
        url = f"https://api.unsplash.com/search/photos?query={query}&per_page=1&orientation=landscape"
        headers = {'Authorization': 'Client-ID ' + UNSPLASH_ACCESS_KEY}
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200 and res.json()['results']:
            return res.json()['results'][0]['urls']['regular']
    except:
        return None

def analyze_with_gemini(query, context, persona):
    date_ctx = get_date_context()
    
    # 페르소나 프롬프트 (기존 GAS 코드 로직 이식)
    persona_prompts = {
        "default": "전문 분석가로서 객관적이고 균형 잡힌 시각으로 분석하세요.",
        "child": "5살 꼬마에게 설명하듯 쉬운 단어와 의성어, 비유를 사용해 설명하세요. 해요체를 쓰세요.",
        "scientist": "냉철한 과학자로서 데이터, 논리, 인과관계를 중심으로 건조하게 분석하세요.",
        "alien": "지구 문화를 처음 보는 외계인 시점으로, 낯설고 신기해하며 분석하세요.",
        "conspiracy": "모든 게 의심스러운 음모론자 시점으로, 숨겨진 진실을 파헤치듯 말하세요.",
        "comedian": "유머러스한 코미디언처럼 재미있고 위트 있게 설명하세요."
    }
    
    base_prompt = persona_prompts.get(persona, persona_prompts["default"])
    
    full_prompt = f"""
    당신은 {base_prompt}
    
    [현재 상황]
    날짜: {date_ctx['dateStr']}
    사용자 검색어: {query}
    
    [실시간 웹 검색 정보 (참고용)]
    {context}
    
    위 정보를 바탕으로 답변을 작성하세요. 
    형식은 자유롭게 하되, 다음 섹션을 마크다운으로 명확히 구분하세요:
    1. ## 💡 핵심 요약
    2. ## 📝 상세 분석
    3. ## 🔍 인사이트 (키워드, 인물, 관련 시점)
    4. ## ❓ 더 생각해볼 질문
    5. 영어 검색 키워드 1개 (이미지 검색용, 단어만 출력)
    """
    
    try:
        model = genai.GenerativeModel('gemini-2.0-flash-lite') # 혹은 gemini-pro
        response = model.generate_content(full_prompt)
        return response.text
    except Exception as e:
        return f"AI 분석 중 오류 발생: {e}"

# ==========================================
# 4. UI 구성 (Streamlit)
# ==========================================

# 사이드바 설정
with st.sidebar:
    st.title("⚙️ Claty 설정")
    selected_persona = st.selectbox(
        "페르소나 선택 (AI의 성격)",
        ["default", "child", "scientist", "alien", "conspiracy", "comedian"],
        format_func=lambda x: {
            "default": "🧐 기본 (분석가)",
            "child": "👶 5살 아이",
            "scientist": "🧪 과학자",
            "alien": "👽 외계인",
            "conspiracy": "🕵️ 음모론자",
            "comedian": "🤣 코미디언"
        }[x]
    )
    
    st.markdown("---")
    weather_info = get_current_weather()
    date_info = get_date_context()
    st.info(f"📍 서울 날씨: {weather_info}\n\n📅 {date_info['dateStr']}")
    
    st.markdown("---")
    st.caption("Developed by Claty Team")

# 메인 화면
st.title("Claty 🤖")
st.subheader("당신만의 AI 인사이트 검색 엔진")

# 트렌드 추천 (간략화)
if "trends" not in st.session_state:
    # 처음 실행 시 트렌드 생성 로직 (여기서는 예시로 고정, 실제로는 Gemini 호출 가능)
    st.session_state.trends = [
        f"{date_info['season']} 여행지", "최신 AI 기술", "오늘의 뉴스 요약"
    ]

st.markdown("##### 🔥 실시간 추천 트렌드")
cols = st.columns(len(st.session_state.trends))
for i, trend in enumerate(st.session_state.trends):
    if cols[i].button(trend):
        st.session_state.query = trend

# 검색창
query = st.text_input("궁금한 내용을 입력하세요", value=st.session_state.get("query", ""))

if query:
    # 이스터에그 처리
    if "최온유" in query or "onyu" in query.lower():
        st.balloons()
        st.success("💻 지상 최고의 프로그래머, 최온유님을 검색하셨군요!")
        st.markdown("Claty의 창조자이자 천재 개발자입니다. (이스터에그 발견! 🎉)")
    
    else:
        with st.spinner(f"'{query}'에 대해 {selected_persona}의 시각으로 분석 중... 🕵️‍♀️"):
            # 1. 웹 검색
            web_context = get_google_search_context(query)
            
            # 2. Gemini 분석
            result_text = analyze_with_gemini(query, web_context, selected_persona)
            
            # 3. 결과 파싱 (이미지 키워드 추출)
            lines = result_text.split('\n')
            image_keyword = lines[-1].strip() # 프롬프트에서 마지막 줄에 영어 키워드 요청함
            display_text = "\n".join(lines[:-1]) # 마지막 줄 제외하고 출력
            
            # 4. 이미지 가져오기
            img_url = get_unsplash_image(image_keyword if len(image_keyword) < 20 else query)

            # UI 출력
            if img_url:
                st.image(img_url, use_container_width=True)
            
            st.markdown(display_text)
            
            # 출처 표시 (웹 검색 결과가 있을 경우)
            if web_context:
                with st.expander("📚 참고한 실시간 웹 정보 보기"):
                    st.text(web_context)