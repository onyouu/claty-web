from flask import Flask, request, jsonify, render_template
import requests
import json
import datetime
import google.generativeai as genai
import time
from functools import lru_cache

app = Flask(__name__)

# ==========================================
# 🔑 API 키 설정 (여기에 본인 키 입력)
# ==========================================
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"
SEARCH_API_KEY = "YOUR_SEARCH_API_KEY"
SEARCH_ENGINE_ID = "YOUR_SEARCH_ENGINE_ID"
UNSPLASH_ACCESS_KEY = "YOUR_UNSPLASH_KEY"

# Gemini 설정
genai.configure(api_key=GEMINI_API_KEY)

# ==========================================
# 🛠️ 헬퍼 함수 (Code.gs 로직 1:1 이식)
# ==========================================

def get_date_context():
    now = datetime.datetime.now()
    days = ['월', '화', '수', '목', '금', '토', '일']
    month = now.month
    
    season = '겨울'
    if 3 <= month <= 5: season = '봄'
    elif 6 <= month <= 8: season = '여름'
    elif 9 <= month <= 11: season = '가을'
    
    return {
        "dateStr": f"{now.year}년 {now.month}월 {now.day}일 ({days[now.weekday()]})",
        "year": now.year,
        "month": now.month,
        "day": now.day,
        "season": season
    }

def get_current_weather():
    try:
        url = 'https://api.open-meteo.com/v1/forecast?latitude=37.5665&longitude=126.9780&current=temperature_2m,weather_code&timezone=Asia%2FSeoul'
        res = requests.get(url, timeout=5).json()
        temp = round(res['current']['temperature_2m'])
        code = res['current']['weather_code']
        
        # WMO Weather interpretation codes (간략화)
        condition = '알 수 없음'
        if code == 0: condition = '맑음'
        elif code in [1, 2, 3]: condition = '구름 조금/흐림'
        elif code in [45, 48]: condition = '안개'
        elif code >= 95: condition = '뇌우'
        elif code >= 80: condition = '소나기'
        elif code >= 61: condition = '비'
        elif code >= 71: condition = '눈'
        elif code >= 51: condition = '이슬비'
        
        return {"temp": temp, "condition": condition, "city": "서울"}
    except Exception as e:
        print(f"날씨 API 오류: {e}")
        return {"temp": None, "condition": None, "city": "서울"}

def get_realtime_web_context(search_term):
    if not SEARCH_API_KEY or not SEARCH_ENGINE_ID:
        return None
    try:
        url = f"https://www.googleapis.com/customsearch/v1?key={SEARCH_API_KEY}&cx={SEARCH_ENGINE_ID}&q={search_term}&num=3"
        res = requests.get(url, timeout=8)
        if res.status_code == 200:
            data = res.json()
            if 'items' in data:
                return "\n\n".join([f"{item['title']}: {item['snippet']}" for item in data['items']])
    except Exception as e:
        print(f"웹 검색 실패: {e}")
    return None

def get_background_image(search_term):
    if not UNSPLASH_ACCESS_KEY: return None
    try:
        url = f"https://api.unsplash.com/search/photos?query={search_term}&per_page=1&orientation=landscape"
        headers = {'Authorization': 'Client-ID ' + UNSPLASH_ACCESS_KEY}
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if data['results']:
                return data['results'][0]['urls']['regular']
    except Exception as e:
        print(f"이미지 로드 실패: {e}")
    return None

# ==========================================
# 🧠 파싱 로직 (Code.gs의 parseGeminiResponse 복원)
# ==========================================
def extract_section(full_text, start_tag, end_tag):
    start_index = full_text.find(start_tag)
    end_index = full_text.find(end_tag)
    if start_index != -1 and end_index != -1 and end_index > start_index:
        return full_text[start_index + len(start_tag):end_index].strip()
    return ""

def parse_insights(text):
    insights = {"keywords": [], "people": [], "dates": []}
    if not text: return insights
    
    for line in text.split('\n'):
        if ':' not in line: continue
        key, value = line.split(':', 1)
        key = key.strip()
        value = value.strip()
        
        if not value or value.lower() in ['없음', 'none', '없어요', '미발견', '불명']: continue
        
        items = [item.strip() for item in value.split(',') if item.strip()]
        
        if '키워드' in key or 'keyword' in key.lower():
            insights['keywords'] = items
        elif '인물' in key or '기관' in key or 'people' in key.lower():
            insights['people'] = items
        elif '날짜' in key or '사건' in key or 'date' in key.lower():
            insights['dates'] = items
    return insights

def parse_smart_links(text):
    if not text or text.lower().strip() in ['없음', 'none']: return []
    links = []
    for line in text.split('\n'):
        if '::' in line:
            parts = line.split('::')
            if len(parts) == 2 and parts[1].strip().startswith('http'):
                links.append({"title": parts[0].strip().replace('"', ''), "url": parts[1].strip()})
    return links

def parse_questions(text):
    if not text: return []
    questions = []
    for line in text.split('\n'):
        clean_line = line.strip()
        # 숫자 제거 (1. 질문 -> 질문)
        import re
        clean_line = re.sub(r'^\d+\.\s*', '', clean_line)
        if len(clean_line) > 3 and clean_line not in ['없음', 'None']:
            questions.append(clean_line)
    return questions

# ==========================================
# 🤖 Gemini 로직 (프롬프트 & 페르소나 완벽 복원)
# ==========================================
def get_gemini_insights(search_term, context, persona):
    date_ctx = get_date_context()
    
    prompts = {
        'child': f"""너는 5살 꼬마야! 세상이 너무 신기해!
오늘: {date_ctx['dateStr']}
궁금한 거: {search_term}{f'\n\n어른들이 알려준 이야기:\n{context}' if context else ''}

5살 말투로 답변해줘:
- 해요 체 사용
- 동물이나 장난감으로 비유
- 어려운 말 쉽게 풀어서

답변 형식:
SUMMARY_START
(2-3문장으로 쉽게 설명)
SUMMARY_END
DETAILS_START
(5-7줄로 재밌는 비유 사용해서 설명)
DETAILS_END
INSIGHTS_START
키워드: (쉬운 단어 3-5개)
주요 인물: (사람 이름, 없으면 "없어요")
관련 날짜: (언제 일어났는지, 없으면 "없어요")
INSIGHTS_END
QUESTIONS_START
1. (왜 그래요?)
2. (어떻게 돼요?)
3. (더 궁금해요!)
QUESTIONS_END
LINKS_START
(링크가 있으면 "제목::URL" 형식, 없으면 "없어요")
LINKS_END
IMAGE_PROMPT_START
(영어 단어 2-3개)
IMAGE_PROMPT_END""",

        'scientist': f"""당신은 냉철한 과학자입니다. 감정을 배제하고 데이터와 논리로만 분석하세요.
Date: {date_ctx['dateStr']}
Subject: {search_term}{f'\n\nData Source:\n{context}' if context else ''}

분석 원칙:
- 감정 배제, 데이터 중심
- 수치와 통계 사용, 과학적 용어 사용
- 논리적 인과관계 분석

답변 형식:
SUMMARY_START
(2-3문장, 팩트와 수치 중심)
SUMMARY_END
DETAILS_START
(5-7줄, 데이터 기반 논리적 분석)
DETAILS_END
INSIGHTS_START
키워드: (과학 용어 3-5개)
주요 인물: (연구자/기관, 없으면 "None")
관련 날짜: (날짜/사건, 없으면 "None")
INSIGHTS_END
QUESTIONS_START
1. (가설 기반 질문)
2. (인과 분석 질문)
3. (후속 연구 질문)
QUESTIONS_END
LINKS_START
("제목::URL" 형식, 없으면 "None")
LINKS_END
IMAGE_PROMPT_START
(영어 키워드 2-3개)
IMAGE_PROMPT_END""",

        'alien': f"""당신은 안드로메다 은하에서 온 외계인입니다. 지구 문화를 처음 관찰하는 시각으로 분석하세요.
지구 시간: {date_ctx['dateStr']}
관찰 대상: {search_term}{f'\n\n지구인 데이터:\n{context}' if context else ''}

외계인 관찰법:
- 모든 걸 처음 보는 외부자 시각
- "지구에서는", "흥미롭게도 인간들은" 사용
- 당연한 것도 신기하게 표현

답변 형식:
SUMMARY_START
(2-3문장, 외계인의 첫인상)
SUMMARY_END
DETAILS_START
(5-7줄, 지구 문화를 외부자로 분석)
DETAILS_END
INSIGHTS_START
키워드: (지구 용어 3-5개)
주요 인물: (관찰된 개체, 없으면 "미발견")
관련 날짜: (시간 좌표, 없으면 "미발견")
INSIGHTS_END
QUESTIONS_START
1. (외계인의 의문)
2. (지구 문화 질문)
3. (비교 분석 질문)
QUESTIONS_END
LINKS_START
("제목::URL" 형식, 없으면 "접속 불가")
LINKS_END
IMAGE_PROMPT_START
(영어 키워드 2-3개)
IMAGE_PROMPT_END""",

        'conspiracy': f"""당신은 수상한 음모론자입니다. 모든 사건 뒤에 숨겨진 진실을 의심하세요. 단, 사실에 기반한 재미있는 음모론만 제시하세요.
날짜: {date_ctx['dateStr']}
사건: {search_term}{f'\n\n공식 발표:\n{context}' if context else ''}

음모론적 분석:
- "겉으로는 하지만 진실은" 구조 사용
- 숨겨진 의도와 연결고리 제시
- "우연이 아니다", "의문점" 강조

답변 형식:
SUMMARY_START
(2-3문장, 숨겨진 진실 제시)
SUMMARY_END
DETAILS_START
(5-7줄, 의심스러운 연결고리)
DETAILS_END
INSIGHTS_START
키워드: (의심 키워드 3-5개)
주요 인물: (배후 세력?, 없으면 "불명")
관련 날짜: (의심스러운 시점, 없으면 "불명")
INSIGHTS_END
QUESTIONS_START
1. (의혹 제기)
2. (배후 질문)
3. (진실 추적)
QUESTIONS_END
LINKS_START
("제목::URL" 형식, 없으면 "은폐됨")
LINKS_END
IMAGE_PROMPT_START
(영어 키워드 2-3개)
IMAGE_PROMPT_END""",

        'comedian': f"""당신은 유머 감각 넘치는 코미디언입니다. 모든 주제를 재미있고 가볍게 풀어내세요.
날짜: {date_ctx['dateStr']}
주제: {search_term}{f'\n\n정보:\n{context}' if context else ''}

코미디언 스타일:
- 유머러스하고 가벼운 톤 ("ㅋㅋㅋ", "근데 진짜")
- 재미있는 비유와 과장

답변 형식:
SUMMARY_START
(2-3문장, 재미있게 요약)
SUMMARY_END
DETAILS_START
(5-7줄, 유머 섞어 설명)
DETAILS_END
INSIGHTS_START
키워드: (핵심 키워드 3-5개)
주요 인물: (관련 인물, 없으면 "없음")
관련 날짜: (날짜/사건, 없으면 "없음")
INSIGHTS_END
QUESTIONS_START
1. (재미있는 질문)
2. (궁금한 질문)
3. (웃긴 질문)
QUESTIONS_END
LINKS_START
("제목::URL" 형식, 없으면 "없음")
LINKS_END
IMAGE_PROMPT_START
(영어 키워드 2-3개)
IMAGE_PROMPT_END""",

        'journalist': f"""당신은 프로 기자입니다. 팩트를 빠르고 정확하게 전달하세요.
날짜: {date_ctx['dateStr']}
취재 주제: {search_term}{f'\n\n취재 내용:\n{context}' if context else ''}

기자 보도 원칙:
- 육하원칙 (누가, 언제, 어디서, 무엇을, 어떻게, 왜)
- 짧고 명확한 문장, 팩트 우선

답변 형식:
SUMMARY_START
(2-3문장, 속보 스타일)
SUMMARY_END
DETAILS_START
(5-7줄, 육하원칙 기반 상세 보도)
DETAILS_END
INSIGHTS_START
키워드: (핵심 키워드 3-5개)
주요 인물: (관련 인물/기관, 없으면 "없음")
관련 날짜: (발생 일시, 없으면 "없음")
INSIGHTS_END
QUESTIONS_START
1. (핵심 질문)
2. (후속 질문)
3. (영향 질문)
QUESTIONS_END
LINKS_START
("제목::URL" 형식, 없으면 "없음")
LINKS_END
IMAGE_PROMPT_START
(영어 키워드 2-3개)
IMAGE_PROMPT_END""",

        'default': f"""당신은 전문 분석가입니다. 객관적이고 균형잡힌 시각으로 분석하세요.
날짜: {date_ctx['dateStr']}
주제: {search_term}{f'\n\n웹 검색 결과:\n{context}\n위 정보를 최우선 참조하세요.' if context else ''}

분석 원칙:
- 객관적이고 균형잡힌 시각
- 최신 정보 우선 ({date_ctx['year']}년 {date_ctx['month']}월)

답변 형식:
SUMMARY_START
(2-3문장 요약)
SUMMARY_END
DETAILS_START
(5-7줄 체계적 분석)
DETAILS_END
INSIGHTS_START
키워드: (핵심 키워드 3-5개)
주요 인물: (관련자, 없으면 "없음")
관련 날짜: (날짜/사건, 없으면 "없음")
INSIGHTS_END
QUESTIONS_START
1. (심화 질문)
2. (영향 분석)
3. (미래 전망)
QUESTIONS_END
LINKS_START
("제목::URL" 형식, 없으면 "없음")
LINKS_END
IMAGE_PROMPT_START
(영어 키워드 2-3개)
IMAGE_PROMPT_END"""
    }

    full_prompt = prompts.get(persona, prompts['default'])

    try:
        model = genai.GenerativeModel('gemini-2.0-flash-lite')
        response = model.generate_content(full_prompt)
        text = response.text

        # 파싱 로직 적용
        sections = {
            'SUMMARY': extract_section(text, 'SUMMARY_START', 'SUMMARY_END'),
            'DETAILS': extract_section(text, 'DETAILS_START', 'DETAILS_END'),
            'INSIGHTS': extract_section(text, 'INSIGHTS_START', 'INSIGHTS_END'),
            'QUESTIONS': extract_section(text, 'QUESTIONS_START', 'QUESTIONS_END'),
            'LINKS': extract_section(text, 'LINKS_START', 'LINKS_END'),
            'IMAGE_PROMPT': extract_section(text, 'IMAGE_PROMPT_START', 'IMAGE_PROMPT_END'),
        }

        return {
            "summary": sections['SUMMARY'] or "요약을 생성할 수 없습니다.",
            "details": sections['DETAILS'] or "",
            "insights": parse_insights(sections['INSIGHTS']),
            "questions": parse_questions(sections['QUESTIONS']),
            "links": parse_smart_links(sections['LINKS']),
            "imagePrompt": sections['IMAGE_PROMPT']
        }

    except Exception as e:
        return {"error": f"Gemini 분석 실패: {str(e)}"}

# ==========================================
# 🌐 API 라우트 (웹사이트 연결)
# ==========================================

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/new_examples', methods=['POST'])
def new_examples():
    # 1. 환경 변수 및 프롬프트 설정 (Code.gs getAITrendKeywords 로직)
    date_ctx = get_date_context()
    weather = get_current_weather()
    weather_info = f"현재 {weather['city']} 날씨: {weather['temp']}°C, {weather['condition']}" if weather['temp'] else ''

    prompt = f"""당신은 대한민국의 실시간 뉴스와 트렌드를 정확히 파악하는 AI 트렌드 분석가입니다.

현재 시점: {date_ctx['dateStr']}
계절: {date_ctx['season']}
{weather_info}

절대 원칙:
- 반드시 {date_ctx['year']}년 {date_ctx['month']}월 기준의 실시간 최신 정보만 사용하세요
- 과거 정보나 일반적인 주제는 절대 선정하지 마세요
- 현재 진행형이거나 최근 화제가 된 이슈만 선택하세요

임무: 지금 이 순간 대한민국에서 실제로 검색되고 있는 주제 3가지를 선정하세요.

선정 기준 우선순위:
1. 실시간 속보 - 최근 발생한 긴급 뉴스
2. 화제의 인물 사건 - 지금 사람들이 관심 갖는 이슈
3. 현재 진행 이벤트 - 지금 열리고 있는 행사, 경기
4. 계절 트렌드 - {date_ctx['season']} {date_ctx['month']}월 특화 주제
5. 기술 과학 - 최근 발표된 신기술, 연구 결과

필수 조건:
- 검색어는 구체적이고 친근하게
- 너무 딱딱하거나 날짜만 강조하지 말고 자연스럽게
- 일반적 주제는 피하세요
- 다양한 카테고리 분산

출력 형식: 반드시 아래 JSON 형식만 출력하세요. 다른 설명이나 마크다운 없이 순수 JSON만 출력하세요.

[
  {{"query": "자연스러운 검색어 1", "display": "짧은 표시명 1"}},
  {{"query": "자연스러운 검색어 2", "display": "짧은 표시명 2"}},
  {{"query": "자연스러운 검색어 3", "display": "짧은 표시명 3"}}
]"""

    try:
        model = genai.GenerativeModel('gemini-2.0-flash-lite')
        response = model.generate_content(prompt)
        cleaned_json = response.text.replace('```json', '').replace('```', '').strip()
        # JSON 파싱 보정 (대괄호 부분만 추출)
        start = cleaned_json.find('[')
        end = cleaned_json.rfind(']')
        if start != -1 and end != -1:
            cleaned_json = cleaned_json[start:end+1]
            trends = json.loads(cleaned_json)
            return jsonify({"issues": trends, "diagnostic": None})
    except Exception as e:
        print(f"트렌드 오류: {e}")
    
    # 실패 시 Fallback
    return jsonify({
        "issues": [
            {"query": f"{date_ctx['season']} 여행지 추천", "display": f"{date_ctx['season']} 여행"},
            {"query": "최신 AI 기술 뉴스", "display": "AI 기술"},
            {"query": "건강 관리 방법", "display": "건강 정보"}
        ],
        "diagnostic": "Fallback Data"
    })

@app.route('/api/search', methods=['POST'])
def search():
    data = request.json
    query = data.get('query')
    persona = data.get('persona', 'default')
    
    if not query: return jsonify({"error": "검색어가 없습니다."})

    # 🥚 이스터에그 (Code.gs 로직 그대로)
    lower_term = query.lower().strip()
    if '최온유' in lower_term or 'onyu' in lower_term:
        return jsonify({
            "summary": "지상 최고의 프로그래머. 💻✨",
            "details": "Claty AI 인사이트 엔진에 자신의 이름을 이스터에그로 심을 정도의 실력자입니다.\n\n그의 코드는 예술과도 같습니다. 완벽한 아키텍처, 깔끔한 로직, 그리고 사용자 경험을 최우선으로 생각하는 철학이 담겨 있습니다.\n\nClaty는 그의 천재성의 결정체입니다.",
            "insights": {"keywords": ['코딩', '열정', '천재성', '마에스트로', '혁신'], "people": ['Claty'], "dates": ['2010년 5월 30일 탄생']},
            "questions": ["최온유는 어떻게 세계 3대 부자가 되었을까?", "최온유의 다음 프로젝트는 무엇일까?", "최온유는 어쩌다 일론머스크의 선택을 받게 되었을까?"],
            "links": [{"title": "최온유 소개", "url": "https://ko.wikipedia.org/wiki/%EC%8B%A0"}, {"title": "최온유의 행적들", "url": "https://roentgenium1.tistory.com/"}],
            "isSpecial": 'onyu',
            "backgroundImageUrl": None
        })
    
    if 'claty' in lower_term:
        return jsonify({
            "summary": "지상 최고의 AI 인사이트 엔진입니다. 😎",
            "details": "'최온유'라는 천재 개발자에 의해 탄생했습니다. 저는 Gemini AI, Google Search API, Unsplash 등 최신 기술을 활용하여 실시간으로 세상의 정보를 분석하고 인사이트를 제공합니다.\n\n음성 검색, 페르소나 검색, 다국어 번역, 다크모드 지원 등 다양한 기능으로 더 나은 사용자 경험을 제공합니다.",
            "insights": {"keywords": ['혁신', '지능', '창의성', 'AI', '미래'], "people": ['최온유','구글'], "dates": ['2025년 개발']},
            "questions": ["Claty는 어떻게 만들어졌나요?", "Claty를 만든 개발자는 누구인가요?", "Claty의 기능은 무엇인가요?", "claty는 어떻게 Google을 뛰어넘었나요?"],
            "links": [{"title": "Claty 소개", "url": "https://www.miricanvas.com/v2/design2/32e9faf6-3263-4bb0-bd9d-046458579ca4"}],
            "isSpecial": 'claty',
            "backgroundImageUrl": None
        })

    # 1. 실시간 웹 검색
    context = get_realtime_web_context(query)
    
    # 2. Gemini 분석 (페르소나 적용)
    result = get_gemini_insights(query, context, persona)
    
    if "error" in result:
        return jsonify(result)

    # 3. 배경 이미지
    bg_url = None
    if result.get('imagePrompt'):
        bg_url = get_background_image(result['imagePrompt'])
    elif result['insights']['keywords']:
        bg_url = get_background_image(result['insights']['keywords'][0])
        
    result['backgroundImageUrl'] = bg_url
    result['isSpecial'] = False
    
    return jsonify(result)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
