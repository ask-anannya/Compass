from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from routes import ingest, websocket, text, pdf, chat, diagram, session

app = FastAPI(title='Compass')

app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])
app.include_router(ingest.router)
app.include_router(websocket.router)
app.include_router(text.router)
app.include_router(pdf.router)
app.include_router(chat.router)
app.include_router(diagram.router)
app.include_router(session.router)

app.mount('/', StaticFiles(directory='frontend', html=True), name='frontend')
