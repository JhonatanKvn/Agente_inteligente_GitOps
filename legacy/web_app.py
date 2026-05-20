import json
import os

import streamlit as st
from dotenv import load_dotenv

from grader import evaluate_demo, evaluate_with_ocr_space
from memory import init_db, list_evaluations, save_evaluation


load_dotenv()
init_db()

st.set_page_config(page_title='Evaluador OCR de Codigo Manuscrito', page_icon='AI', layout='wide')

if 'last_result' not in st.session_state:
    st.session_state['last_result'] = None
if 'last_eval_id' not in st.session_state:
    st.session_state['last_eval_id'] = None

st.title('Evaluador OCR de Codigo Manuscrito')
st.caption('Evalua codigo manuscrito con OCR.Space y guarda historial por estudiante.')

with st.sidebar:
    st.header('Configuracion')
    api_mode = st.selectbox('Modo de evaluacion', ['Demo gratis (sin API)', 'API OCR.Space (gratis)'], key='mode')
    max_score = st.number_input('Nota maxima', min_value=1.0, max_value=100.0, value=5.0, step=0.5, key='max_score')
    if st.button('Nueva evaluacion', use_container_width=True):
        for key in ['student_name', 'student_code', 'rubric_text', 'uploaded_image']:
            if key in st.session_state:
                del st.session_state[key]
        st.session_state['last_result'] = None
        st.session_state['last_eval_id'] = None
        st.rerun()

st.subheader('Paso 1: Datos del estudiante')
c1, c2 = st.columns(2)
with c1:
    student_name = st.text_input('Nombre del estudiante', key='student_name')
with c2:
    student_code = st.text_input('Codigo o ID del estudiante (opcional)', key='student_code')

st.subheader('Paso 2: Rubricas de evaluacion')
rubric_text = st.text_area(
    'Define criterios y pesos. Ejemplo:',
    value=(
        'Criterio: Logica del algoritmo (40%)\n'
        'Criterio: Sintaxis y estructura en Python (30%)\n'
        'Criterio: Buenas practicas y legibilidad (30%)\n'
    ),
    height=180,
    key='rubric_text',
)

st.subheader('Paso 3: Subir foto del codigo escrito a mano')
uploaded = st.file_uploader('Imagen del codigo (max 1MB)', type=['jpg', 'jpeg', 'png', 'webp'], key='uploaded_image')

st.subheader('Paso 4: Evaluar')
if st.button('Evaluar entrega', type='primary'):
    if not student_name.strip():
        st.error('Debes escribir el nombre del estudiante.')
        st.stop()
    if not rubric_text.strip():
        st.error('Debes escribir las rubricas de evaluacion.')
        st.stop()
    if not uploaded:
        st.error('Debes subir una imagen del codigo.')
        st.stop()

    with st.spinner('Evaluando...'):
        try:
            if api_mode == 'Demo gratis (sin API)':
                result = evaluate_demo(rubric_text=rubric_text, max_score=max_score)
            else:
                api_key = os.getenv('OCRSPACE_API_KEY', 'helloworld').strip()
                result = evaluate_with_ocr_space(
                    api_key=api_key,
                    rubric_text=rubric_text,
                    image_bytes=uploaded.getvalue(),
                    filename=uploaded.name,
                    max_score=max_score,
                )
        except Exception as e:
            st.error(f'No se pudo evaluar la entrega: {e}')
            st.stop()

    eval_id = save_evaluation(
        {
            'student_name': student_name.strip(),
            'student_code': student_code.strip(),
            'mode': 'demo' if api_mode == 'Demo gratis (sin API)' else 'ocrspace',
            'score': result.score,
            'max_score': result.max_score,
            'feedback': result.feedback,
            'code_transcription': result.code_transcription,
            'strengths_json': json.dumps(result.strengths, ensure_ascii=False),
            'improvements_json': json.dumps(result.improvements, ensure_ascii=False),
            'rubric_breakdown_json': json.dumps(result.rubric_breakdown, ensure_ascii=False),
            'rubric_text': rubric_text,
            'image_filename': uploaded.name,
        }
    )
    st.session_state['last_result'] = result
    st.session_state['last_eval_id'] = eval_id
    st.success(f'Evaluacion completada y guardada en historial (ID {eval_id}).')

result = st.session_state.get('last_result')
if result:
    c1, c2 = st.columns([1, 2])
    with c1:
        st.metric('Nota', f'{result.score:.2f} / {result.max_score:.2f}')
    with c2:
        st.write('### Retroalimentacion')
        st.write(result.feedback)

    st.write('### Fortalezas')
    for item in result.strengths or ['Sin fortalezas registradas.']:
        st.write(f'- {item}')

    st.write('### Oportunidades de mejora')
    for item in result.improvements or ['Sin mejoras registradas.']:
        st.write(f'- {item}')

    st.write('### Desglose por rubrica')
    if result.rubric_breakdown:
        st.dataframe(result.rubric_breakdown, use_container_width=True)
    else:
        st.info('No hubo desglose por criterio.')

    with st.expander('Ver transcripcion del codigo'):
        st.code(result.code_transcription or '', language='python')

st.divider()
st.subheader('Historial de evaluaciones por estudiante')
filter_name = st.text_input('Filtrar por nombre', '')
history = list_evaluations(student_name=filter_name, limit=50)
if history:
    st.dataframe(history, use_container_width=True)
else:
    st.info('Aun no hay evaluaciones guardadas.')
