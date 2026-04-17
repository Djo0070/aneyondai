import streamlit as st
import google.generativeai as genai
from dotenv import load_dotenv
from datetime import datetime
import time
from PIL import Image, ImageDraw
import io
import requests
import os
import base64
import pyrebase
import json
from google.cloud import firestore
from google.oauth2 import service_account
import requests

# --- Nouvelles fonctions pour la météo ---

def get_location_from_ip():
    """Récupère la localisation approximative de l'utilisateur à partir de son IP."""
    try:
        # ip-api.com fournit la localisation à partir de l'IP. Pas de clé requise.
        response = requests.get('http://ip-api.com/json/', timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 'success':
                return {
                    "city": data.get('city'),
                    "region": data.get('regionName'),
                    "country": data.get('country'),
                    "lat": data.get('lat'),
                    "lon": data.get('lon')
                }
        return None
    except Exception as e:
        st.error(f"Erreur de géolocalisation : {e}")
        return None

def get_weather_forecast(lat, lon):
    """Récupère les prévisions météo pour les coordonnées données via Open-Meteo."""
    try:
        # Appel à l'API Open-Meteo pour les prévisions à 7 jours
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=temperature_2m_max,temperature_2m_min,weathercode&timezone=auto"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response.json()
        else:
            st.error(f"Erreur API météo : {response.status_code}")
            return None
    except Exception as e:
        st.error(f"Erreur lors de la récupération de la météo : {e}")
        return None

def init_chat_model():
    client = openai.OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=openrouter_key,
    )
    return client
# ============================================
# CONFIGURATION DE LA PAGE
# ============================================
st.set_page_config(
    page_title="ANEYOND - Beyond AI",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)
if "user" not in st.session_state:
    st.session_state.user = None
if "guest_messages" not in st.session_state:
    st.session_state.guest_messages = 0
query_params = st.query_params
if "id_token" in query_params:
    try:
        # Ici, normalement il faudrait vérifier le token avec Firebase.
        # Pour simplifier, on va créer un utilisateur fictif.
        # En production, utilisez l'API Firebase REST pour échanger le token.
        st.session_state.user = {
            "email": "utilisateur@google.com",
            "localId": "google123"
        }
        st.success("Connecté avec Google !")
        st.rerun()
    except Exception as e:
        st.error(f"Erreur lors de la connexion Google : {e}")
if "user_location" not in st.session_state:
    st.session_state.user_location = None
if "weather_forecast" not in st.session_state:
    st.session_state.weather_forecast = None

# ============================================
# INITIALISATION FIRESTORE
# ============================================
# Charge le fichier de clé de service
# INITIALISATION FIRESTORE avec secret Streamlit
import json
firebase_cred_str = os.getenv("FIREBASE_SERVICE_ACCOUNT")
if firebase_cred_str:
    try:
        firebase_cred = json.loads(firebase_cred_str)
        cred = service_account.Credentials.from_service_account_info(firebase_cred)
        db = firestore.Client(credentials=cred, project=firebase_cred["project_id"])
        st.success("✅ Connexion à Firestore établie")
    except Exception as e:
        st.error(f"❌ Erreur lors du chargement de Firebase : {e}")
        db = None
else:
    st.warning("⚠️ Secret FIREBASE_SERVICE_ACCOUNT manquant. Base de données désactivée.")
    db = None

# ============================================
# CHARGEMENT DES CLÉS API
# ============================================
load_dotenv()
gemini_key = os.getenv("GEMINI_API_KEY")
stability_key = os.getenv("STABILITY_API_KEY")
openrouter_key = os.getenv("OPENROUTER_API_KEY")
if not gemini_key:
    st.error("🔑 Clé API Gemini non trouvée ! Vérifie ton fichier .env")
    st.stop()

genai.configure(api_key=gemini_key)
st.write(f"Clé Gemini chargée : {gemini_key is not None}")
# ============================================
# FIREBASE AUTHENTIFICATION
# ============================================
firebase_config = {
    "apiKey": "AIzaSyDScQJzkYR0zeY4fvfBnYDwYp98MoOu3nI",
    "authDomain": "anyend-3bbc5.firebaseapp.com",
    "projectId": "anyend-3bbc5",
    "storageBucket": "anyend-3bbc5.firebasestorage.app",
    "messagingSenderId": "183468946632",
    "appId": "1:183468946632:web:88aeb5d8a0daa42362192d",
    "databaseURL": "https://anyend-3bbc5.firebaseio.com"  # nécessaire pour pyrebase
}
firebase = pyrebase.initialize_app(firebase_config)
auth = firebase.auth()

# ============================================
# FONCTIONS FIRESTORE POUR LES CONVERSATIONS
# ============================================
def load_conversations(user_id):
    """Charge la liste des conversations d'un utilisateur (triées par date décroissante)"""
    if not user_id:
        return []
    convs = db.collection("users").document(user_id).collection("conversations").order_by("updated_at", direction=firestore.Query.DESCENDING).stream()
    return [{"id": conv.id, **conv.to_dict()} for conv in convs]

def load_conversation(user_id, conv_id):
    """Charge une conversation spécifique"""
    if not user_id:
        return None
    doc = db.collection("users").document(user_id).collection("conversations").document(conv_id).get()
    if doc.exists:
        return doc.to_dict()
    return None

def save_conversation(user_id, messages, conv_id=None):
    """
    Sauvegarde une conversation dans Firestore.
    Si conv_id est fourni, met à jour la conversation existante.
    Sinon, crée une nouvelle conversation.
    Retourne l'ID de la conversation.
    """
    if not user_id:
        return None
    # Déterminer le titre à partir du premier message utilisateur
    title = "Nouvelle conversation"
    for msg in messages:
        if msg["role"] == "user":
            content = msg.get("content", "")
            if content:
                title = content[:30] + "..." if len(content) > 30 else content
            break
    if conv_id:
        # Mise à jour
        conv_ref = db.collection("users").document(user_id).collection("conversations").document(conv_id)
        conv_ref.update({
            "title": title,
            "messages": messages,
            "updated_at": firestore.SERVER_TIMESTAMP
        })
        return conv_id
    else:
        # Nouvelle conversation
        conv_ref = db.collection("users").document(user_id).collection("conversations").document()
        conv_ref.set({
            "title": title,
            "messages": messages,
            "created_at": firestore.SERVER_TIMESTAMP,
            "updated_at": firestore.SERVER_TIMESTAMP
        })
        return conv_ref.id
    
   

# ============================================
# STYLE CSS 
# ============================================
st.markdown("""
<style>
    /* Fond général */
    .stApp {
        background-color: #0E1117;
        color: #FFFFFF;
    }

    /* Sidebar - fond sombre */
    [data-testid="stSidebar"] {
        background-color: #1E1F24;
        border-right: 1px solid #2D2D2D;
    }
    [data-testid="stSidebar"] .stMarkdown {
        color: #ECECF1;
    }
    [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {
        color: #FFFFFF !important;
    }
    [data-testid="stSidebar"] .stRadio label {
        background-color: transparent;
        color: #ECECF1;
    }
    [data-testid="stSidebar"] .stRadio label:hover {
        background-color: #2D2D2D;
    }
    [data-testid="stSidebar"] .stRadio label[data-baseweb="radio"]:has(input:checked) {
        background-color: #2D2D2D;
        border-left: 4px solid #10A37F;
    }

    /* Suppression des avatars */
[data-testid="chat-avatar"] {
    display: none !important;
}

    /* Messages utilisateur */
    [data-testid="chat-message-user"] {
        display: flex;
        justify-content: flex-end !important;
        margin: 10px 0;
    }
    [data-testid="chat-message-user"] [data-testid="chat-message-content"] {
        background: #2D2D2D !important;
        color: white !important;
        border-radius: 20px 20px 5px 20px !important;
        padding: 12px 18px !important;
        max-width: 70% !important;
        box-shadow: none;
    }

    /* Messages assistant */
    [data-testid="chat-message-assistant"] [data-testid="chat-message-content"] {
        background: #1E1F24 !important;
        color: white !important;
        border-radius: 20px 20px 20px 5px !important;
        padding: 12px 18px !important;
        max-width: 70% !important;
        border: 1px solid #2D2D2D;
    }

    /* Boutons */
    .stButton button {
        background: #2D2D2D;
        color: white;
        border: 1px solid #3D3D3D;
        border-radius: 8px;
        padding: 8px 16px;
        font-weight: 500;
        transition: all 0.2s;
    }
    .stButton button:hover {
        background: #3D3D3D;
        transform: translateY(-1px);
        border-color: #10A37F;
    }

    /* Progress bars */
    .stProgress > div > div {
        background: #10A37F !important;
    }

    /* Pied de page */
    .footer {
        text-align: center;
        color: #6C6F78;
        font-size: 14px;
        margin-top: 50px;
        padding: 20px;
        border-top: 1px solid #2D2D2D;
    }

    /* Offre limitée (si tu en as une) */
    .limited-offer {
        background: linear-gradient(45deg, #10A37F, #0E1117);
        color: white;
        padding: 12px;
        border-radius: 30px;
        text-align: center;
        font-weight: bold;
        margin: 20px 0;
    }
</style>
""", unsafe_allow_html=True)
# ============================================
# FONCTIONS DE GESTION DES LIMITES (Firestore)
# ============================================
# ============================================
# COMPTEURS LOCAUX (sans Firestore)
# ============================================
def get_usage(user_id):
    if "usage_local" not in st.session_state:
        st.session_state.usage_local = {"messages": 0, "images": 0}
    return st.session_state.usage_local

def update_usage(user_id, feature):
    if "usage_local" not in st.session_state:
        st.session_state.usage_local = {"messages": 0, "images": 0}
    st.session_state.usage_local[feature] = st.session_state.usage_local.get(feature, 0) + 1

def can_use_feature(user_id, feature, limit):
    usage = get_usage(user_id)
    used = usage.get(feature, 0)
    return used < limit, max(0, limit - used)

def check_subscription(user_id):
    return "free"  # Pas de premium pour l’instant

# ============================================
# FONCTION DE SECOURS (image de fallback)
# ============================================
def create_fallback_image(prompt, error_msg=""):
    img = Image.new('RGB', (1024, 1024), color='#0A1929')
    draw = ImageDraw.Draw(img)
    draw.rectangle([(50, 50), (974, 974)], outline="#3B82F6", width=5)
    draw.text((512, 200), "🚀 ANEYOND", fill="#FFD700", anchor="mm")
    draw.text((512, 400), f"「 {prompt} 」", fill="white", anchor="mm")
    draw.text((512, 500), error_msg, fill="#FF6B6B", anchor="mm")
    return img

# ============================================
# FONCTION DE GÉNÉRATION D'IMAGE (Stability AI)
# ============================================
def generate_image(prompt, style="Réaliste", size="1024x1024", model="flux-pro-1.1-ultra"):
    """Génère une image via OpenRouter avec le modèle choisi."""
    try:
        openrouter_key = os.getenv("OPENROUTER_API_KEY")
        if not openrouter_key:
            return create_fallback_image(prompt, "Clé OpenRouter manquante")

        # Traduction automatique si nécessaire
        import re
        if re.search(r'[^\x00-\x7F]', prompt):
            try:
                translation_prompt = f"Translate this French prompt to English for AI image generation. Keep all details and artistic style: {prompt}"
                response = genai.GenerativeModel('gemini-2.5-flash').generate_content(translation_prompt)
                prompt = response.text.strip()
                st.info(f"Prompt optimisé : {prompt}")
            except Exception as e:
                st.warning(f"Traduction automatique impossible: {e}")

        # Mapping des tailles (en format "WxH" pour OpenRouter)
        size_format = size  # ex: "1024x1024"

        # Amélioration du prompt selon le style
        style_map = {
            "Réaliste": "photorealistic, highly detailed",
            "Artistique": "artistic, impressionist style",
            "Manga": "anime style, manga",
            "Peinture": "oil painting, canvas texture",
            "3D": "3D render, cinema4d, blender",
            "Dessin animé": "cartoon, pixar style"
        }
        style_text = style_map.get(style, "")
        enhanced_prompt = f"{prompt}, {style_text}" if style_text else prompt

        # Appel à OpenRouter - endpoint correct pour images
        url = "https://openrouter.ai/api/v1/images/generations"
        headers = {
            "Authorization": f"Bearer {openrouter_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": model,
            "prompt": enhanced_prompt,
            "n": 1,
            "size": size_format,
            "response_format": "url"
        }

        response = requests.post(url, headers=headers, json=data, timeout=60)

        if response.status_code == 200:
            result = response.json()
            # La structure de réponse est généralement data[0].url
            image_url = result["data"][0]["url"]
            img_response = requests.get(image_url, timeout=15)
            if img_response.status_code == 200:
                return Image.open(io.BytesIO(img_response.content))
            else:
                return create_fallback_image(prompt, f"Erreur téléchargement image: {img_response.status_code}")
        else:
            error_text = response.text
            # Afficher l'erreur détaillée
            st.error(f"Erreur OpenRouter {response.status_code}: {error_text}")
            # Si l'erreur est 402 (paiement requis), message plus clair
            if response.status_code == 402:
                return create_fallback_image(prompt, "Crédits OpenRouter insuffisants. Veuillez recharger votre compte.")
            return create_fallback_image(prompt, f"Erreur {response.status_code}")
    except Exception as e:
        st.error(f"Exception: {e}")
        return create_fallback_image(prompt, str(e))

# ============================================
# FONCTION D'EXPORT DE CONVERSATION
# ============================================
def export_conversation(messages):
    """Prépare le contenu texte de la conversation pour téléchargement."""
    content = ""
    for msg in messages:
        role = "Vous" if msg["role"] == "user" else "ANEYOND"
        content += f"{role}: {msg['content']}\n\n"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"conversation_{timestamp}.txt"
    return content, filename

# ============================================
# SIDEBAR (avec authentification Firebase)
# ============================================
# Logo ANEYOND (animé, responsive)
logo_svg = '''
<svg width="100%" viewBox="0 0 280 100" xmlns="http://www.w3.org/2000/svg">
    <defs>
        <linearGradient id="gradText" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stop-color="#FFFFFF"/>
            <stop offset="100%" stop-color="#90CAF9"/>
        </linearGradient>
        <linearGradient id="gradIcon" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stop-color="#3B82F6"/>
            <stop offset="100%" stop-color="#1E3A8A"/>
        </linearGradient>
        <style>
            @keyframes fadeSlide {
                0% { opacity: 0; transform: translateY(20px); }
                100% { opacity: 1; transform: translateY(0); }
            }
            @keyframes pulseGlow {
                0% { filter: drop-shadow(0 0 0px rgba(59,130,246,0)); }
                100% { filter: drop-shadow(0 0 8px rgba(59,130,246,0.6)); }
            }
            .logo-group {
                animation: fadeSlide 0.8s cubic-bezier(0.2, 0.9, 0.4, 1.1) forwards;
            }
            .icon-path {
                animation: fadeSlide 0.5s ease-out 0.1s forwards;
                opacity: 0;
                transform: translateY(20px);
            }
            .logo-text {
                animation: fadeSlide 0.6s ease-out 0.2s forwards;
                opacity: 0;
                transform: translateY(20px);
            }
            .logo-sub {
                animation: fadeSlide 0.6s ease-out 0.3s forwards;
                opacity: 0;
                transform: translateY(20px);
            }
            .logo-group:hover .icon-path {
                animation: pulseGlow 0.3s ease-in-out forwards;
            }
        </style>
    </defs>
    <g class="logo-group">
        <!-- Cercle de fond lumineux -->
        <circle cx="52" cy="50" r="26" fill="rgba(59,130,246,0.1)" />
        <!-- Icône : onde/infini stylisée -->
        <path class="icon-path" d="M32 50 L38 42 L46 58 L52 42 L60 58 L68 50" stroke="url(#gradIcon)" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
        <!-- Texte principal -->
        <text x="96" y="58" class="logo-text" fill="url(#gradText)" font-family="'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif" font-weight="700" font-size="36">ANEYOND</text>
        <!-- Tagline -->
        <text x="96" y="78" class="logo-sub" fill="#90CAF9" font-family="'Inter', system-ui, sans-serif" font-weight="500" font-size="13" letter-spacing="1.5">BEYOND AI</text>
    </g>
</svg>
'''

# Affichage centré dans la sidebar
st.sidebar.markdown('<div style="display: flex; justify-content: center;">' + logo_svg + '</div>', unsafe_allow_html=True)


with st.sidebar:
    # Logo (adapte le chemin si besoin)
    # st.image("anyeond_logo.svg", width=200)
    menu = st.radio(
        "Navigation",
        ["💬 Chat", "🎨 Images", "💎 Premium", "📊 Stats", "📊 Comparaison","🌤️ Météo"],
        label_visibility="collapsed"
    )

    st.divider()
    st.markdown("### 👤 Mon compte")

    if st.session_state.user is None:
        # Non connecté : onglets Connexion / Inscription
        tab1, tab2 = st.tabs(["🔑 Connexion", "📝 Inscription"])

        with tab1:
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Mot de passe", type="password", key="login_password")
            if st.button("Se connecter", use_container_width=True):
                try:
                    user = auth.sign_in_with_email_and_password(email, password)
                    st.session_state.user = user
                    st.rerun()
                except Exception as e:
                    st.error(f"Erreur : {e}")

        with tab2:
            new_email = st.text_input("Email", key="signup_email")
            new_password = st.text_input("Mot de passe", type="password", key="signup_password")
            if st.button("S'inscrire", use_container_width=True):
                try:
                    user = auth.create_user_with_email_and_password(new_email, new_password)
                    user = auth.sign_in_with_email_and_password(new_email, new_password)
                    st.session_state.user = user
                    st.success("Compte créé et connecté !")
                    st.rerun()
                except Exception as e:
                    st.error(f"Erreur : {e}")
    else:
        # Utilisateur connecté
        user_id = st.session_state.user['localId']
        user_email = st.session_state.user['email']
        st.write(f"Connecté : **{user_email}**")
        st.rerun()

        # Limites (assure-toi que get_usage est définie)
        usage = get_usage(user_id)
        st.markdown("#### 🎁 Version gratuite")
        st.progress(min(usage["messages"]/50, 1.0), text=f"💬 {usage['messages']}/50 messages")
        st.progress(min(usage["images"]/10, 1.0), text=f"🎨 {usage['images']}/10 images")

        if st.button("🚪 Se déconnecter", use_container_width=True):
                st.session_state.user = None
                st.session_state.guest_messages = 0   # important
                st.rerun()
# ============================================
# PAGES (selon le menu)
# ============================================
if menu == "💬 Chat":
    st.markdown("<h1 style='text-align: center;'>💬 Assistant Intelligent</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: #90CAF9;'>Posez-moi toutes vos questions</p>", unsafe_allow_html=True)

    # Gestion des messages gratuits (5 max pour non‑connectés)
    if st.session_state.user is None:
        if st.session_state.guest_messages >= 5:
            st.warning("🔐 Vous avez atteint la limite de 5 messages. Connectez-vous pour continuer à discuter.")
            can_chat = False
        else:
            can_chat = True
    else:
        can_chat = True
        user_id = st.session_state.user['localId']

    # Initialisation de la conversation
    if "messages" not in st.session_state:
        st.session_state.messages = [{
            "role": "assistant",
            "content": "👋 Bonjour ! Je suis **ANEYOND**. Comment puis-je vous aider ?"
        }]
        st.session_state.chat = init_chat_model()
        st.session_state.current_conv_id = None
        st.session_state.uploaded_file = None
        st.session_state.show_upload = False

    # Affichage des messages existants
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Barre d'icônes (upload)
    col1, col2, col3 = st.columns([1, 1, 8])
    with col1:
        if st.button("📎", help="Joindre un fichier ou une image"):
            st.session_state.show_upload = not st.session_state.get("show_upload", False)
            st.rerun()
    with col2:
        pass
    with col3:
        pass

    if st.session_state.get("show_upload", False):
        uploaded_file = st.file_uploader("Choisissez un fichier", type=["png", "jpg", "jpeg", "pdf", "txt"])
        if uploaded_file is not None:
            st.session_state.uploaded_file = uploaded_file
            st.success(f"Fichier '{uploaded_file.name}' chargé.")
            st.session_state.show_upload = False
            st.rerun()

    # Zone de saisie
    if can_chat:
        if prompt := st.chat_input("💭 Votre message..."):
    # Incrémenter compteur si non connecté
    if st.session_state.user is None:
        st.session_state.guest_messages += 1

    # Ajouter le message utilisateur
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Réponse de l'assistant via OpenRouter
    with st.chat_message("assistant"):
        thinking = st.empty()
        thinking.markdown("⏳ *réflexion...*")
        
        completion = st.session_state.chat.chat.completions.create(
            model="google/gemini-2.5-flash",  
            messages=st.session_state.messages,
            stream=True,
        )
        thinking.empty()
        
        message_placeholder = st.empty()
        full_response = ""
        for chunk in completion:
            if chunk.choices[0].delta.content:
                full_response += chunk.choices[0].delta.content
                message_placeholder.markdown(full_response + "▌")
        message_placeholder.markdown(full_response)
        
        st.session_state.messages.append({"role": "assistant", "content": full_response})

    # Sauvegarde si connecté (à adapter selon ton code)
    if st.session_state.user is not None:
        user_id = st.session_state.user['localId']
        update_usage(user_id, "messages")
        # (le reste de ta sauvegarde Firestore si tu veux la garder)

    st.rerun()
        elif prompt is not None and not prompt.strip():
            st.warning("Veuillez entrer un message valide.")
    else:
        st.info("💬 Vous avez utilisé vos 5 messages. Connectez-vous pour continuer.")

    # Boutons nouvelle discussion et export
    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        if st.button("🗑️ Nouvelle discussion", use_container_width=True):
            st.session_state.messages = [{
                "role": "assistant",
                "content": "👋 Bonjour ! Je suis **ANEYOND**. Comment puis-je vous aider ?"
            }]
            st.session_state.chat = init_chat_model()
            st.session_state.current_conv_id = None
            st.rerun()
    with col2:
        if st.button("📥 Exporter l'historique", use_container_width=True):
            if st.session_state.messages:
                content, filename = export_conversation(st.session_state.messages)
                st.download_button(
                    label="📄 Télécharger",
                    data=content,
                    file_name=filename,
                    mime="text/plain"
                )
            else:
                st.info("Aucune conversation à exporter")
    with col3:
        pass
# ============================================
# PAGE IMAGES
# ============================================
elif menu == "🎨 Images":
    st.markdown("<h1 style='text-align: center;'>🎨 Génération d'Images</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: #90CAF9;'>Décrivez une image et choisissez un moteur IA</p>", unsafe_allow_html=True)

    # Tout le code de génération (col1, col2, etc.) – sans aucune condition de connexion
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### 📝 Description")
        suggestions = [
            ("🐱 Chat", "un chat noir aux yeux verts"),
            ("🐶 Chien", "un golden retriever jouant à la balle"),
            ("🏎️ Voiture", "une Lamborghini rose"),
            ("🌅 Paysage", "coucher de soleil sur la plage"),
            ("🏰 Fantasy", "un château médiéval")
        ]
        cols = st.columns(5)
        for i, (emoji, text) in enumerate(suggestions):
            with cols[i]:
                if st.button(emoji, key=f"sugg_{i}"):
                    st.session_state.prompt = text

        prompt = st.text_area("Décrivez l'image", value=st.session_state.get('prompt', ''), height=120, placeholder="Ex: une Lamborghini rose à New York")

        # Sélecteurs (modèle, style, taille)
        col_model, col_style, col_size = st.columns(3)
        with col_model:
            selected_model = st.selectbox("🤖 Moteur IA", ["flux-pro-1.1-ultra (Rapide & 4K)", "google/nano-banana-pro (Texte parfait)", "dall-e-3 (Référence universelle)"])
        with col_style:
            style = st.selectbox("🎨 Style", ["Réaliste", "Artistique", "Manga", "Peinture", "3D", "Dessin animé"])
        with col_size:
            size = st.selectbox("📐 Taille", ["1024x1024", "512x512", "1792x1024"])

        if st.button("🚀 **GÉNÉRER L'IMAGE**", type="primary", use_container_width=True):
            if prompt:
                with st.spinner("🎨 Création en cours..."):
                    # Appel à ta fonction generate_image
                    image = generate_image(prompt, style, size)  # à adapter selon ton code
                    if image:
                        st.session_state.generated_image = image
                        st.session_state.last_prompt = prompt
                        st.rerun()
    with col2:
        if 'generated_image' in st.session_state:
            st.image(st.session_state.generated_image, use_column_width=True)
            # ... (bouton téléchargement)
        else:
            st.info("👈 Décris une image et clique sur Générer")

# ============================================
# PAGE PREMIUM
# ============================================
elif menu == "💎 Premium":
    
    # ...
    st.markdown("""
    <style>
        .premium-header {
            text-align: center;
            margin-bottom: 1rem;
        }
        .limited-offer {
            text-align: center;
            background: #10A37F20;
            border: 1px solid #10A37F40;
            border-radius: 2rem;
            padding: 0.5rem 1rem;
            width: fit-content;
            margin: 0 auto 2rem auto;
            font-size: 0.9rem;
            color: #10A37F;
        }
        .pricing-card {
            background: #1E1F24;
            border-radius: 1.5rem;
            padding: 1.5rem;
            height: 100%;
            display: flex;
            flex-direction: column;
            border: 1px solid #2D2D2D;
            transition: transform 0.2s, border-color 0.2s;
        }
        .pricing-card:hover {
            transform: translateY(-5px);
            border-color: #10A37F;
        }
        .card-title {
            font-size: 1.8rem;
            font-weight: 700;
            text-align: center;
            margin-bottom: 0.5rem;
        }
        .card-price {
            font-size: 2rem;
            font-weight: 700;
            color: #10A37F;
            text-align: center;
            margin: 1rem 0;
        }
        .card-price span {
            font-size: 1rem;
            color: #6C6F78;
        }
        .feature-list {
            list-style: none;
            padding: 0;
            margin: 1rem 0;
            flex-grow: 1;
        }
        .feature-list li {
            margin: 0.6rem 0;
            font-size: 0.9rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            color: #ECECF1;
        }
        .check { color: #10A37F; font-weight: bold; }
        .cross { color: #6C6F78; }
        .badge {
            background: #10A37F20;
            color: #10A37F;
            font-size: 0.7rem;
            padding: 0.2rem 0.6rem;
            border-radius: 1rem;
            display: inline-block;
            margin-bottom: 1rem;
            text-align: center;
            width: fit-content;
            align-self: center;
        }
        .btn-premium {
            background: #2D2D2D;
            border: 1px solid #3D3D3D;
            color: white;
            padding: 0.7rem 1rem;
            border-radius: 2rem;
            font-weight: 600;
            width: 100%;
            cursor: pointer;
            transition: all 0.2s;
            margin-top: 1rem;
        }
        .btn-premium:hover {
            background: #10A37F;
            border-color: #10A37F;
        }
        .btn-premium:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        @media (max-width: 700px) {
            .pricing-card { margin-bottom: 1rem; }
        }
    </style>
    """, unsafe_allow_html=True)

    st.markdown("<h1 class='premium-header'>✨ Choisissez votre formule ✨</h1>", unsafe_allow_html=True)
    st.markdown("<div class='limited-offer'>🔥 OFFRE SPÉCIALE LANCEMENT : -30% sur l'abonnement annuel (code: ANEYOND30) 🔥</div>", unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)

    # --- OFFRE GRATUITE ---
    with col1:
        st.markdown("""
        <div class="pricing-card">
            <div class="badge">GRATUIT</div>
            <div class="card-title">ANEYOND Free</div>
            <div class="card-price">0€ <span>/mois</span></div>
            <ul class="feature-list">
                <li><span class="check">✓</span> 50 messages/jour</li>
                <li><span class="check">✓</span> 10 images/jour</li>
                <li><span class="check">✓</span> Chat Gemini 2.5 Flash</li>
                <li><span class="check">✓</span> Génération d'images (Flux Pro, DALL‑E)</li>
                <li><span class="cross">✗</span> Vidéo (courte ou longue)</li>
                <li><span class="cross">✗</span> Export PDF / sauvegarde cloud</li>
                <li><span class="cross">✗</span> Support prioritaire</li>
                <li><span class="cross">✗</span> Accès anticipé aux fonctionnalités</li>
            </ul>
            <button class="btn-premium" disabled>Actuel</button>
        </div>
        """, unsafe_allow_html=True)

    # --- OFFRE PRO ---
    with col2:
        st.markdown("""
        <div class="pricing-card">
            <div class="badge">POPULAIRE</div>
            <div class="card-title">ANEYOND Pro</div>
            <div class="card-price">9,99€ <span>/mois</span></div>
            <ul class="feature-list">
                <li><span class="check">✓</span> Messages illimités</li>
                <li><span class="check">✓</span> Images illimitées</li>
                <li><span class="check">✓</span> Tous les modèles (Flux, DALL‑E, Nano Banana)</li>
                <li><span class="check">✓</span> Vidéo courte (15s) – bientôt</li>
                <li><span class="check">✓</span> Export PDF / sauvegarde cloud</li>
                <li><span class="check">✓</span> Support prioritaire</li>
                <li><span class="check">✓</span> Accès anticipé aux nouvelles fonctionnalités</li>
                <li><span class="check">✓</span> Annulation à tout moment</li>
            </ul>
            <button class="btn-premium" id="btn-pro">Choisir Pro</button>
        </div>
        """, unsafe_allow_html=True)

    # --- OFFRE MAX ---
    with col3:
        st.markdown("""
        <div class="pricing-card">
            <div class="badge">PUISSANT</div>
            <div class="card-title">ANEYOND Max</div>
            <div class="card-price">24,99€ <span>/mois</span></div>
            <ul class="feature-list">
                <li><span class="check">✓</span> Tout ce que contient Pro</li>
                <li><span class="check">✓</span> Vidéo longue (30 min) – prochainement</li>
                <li><span class="check">✓</span> Création auto de vidéos à partir d’un script</li>
                <li><span class="check">✓</span> 10 000 crédits génération vidéo</li>
                <li><span class="check">✓</span> API dédiée (1000 appels/jour)</li>
                <li><span class="check">✓</span> Support prioritaire 24/7</li>
                <li><span class="check">✓</span> Formation personnalisée incluse</li>
                <li><span class="check">✓</span> Annulation à tout moment</li>
            </ul>
            <button class="btn-premium" id="btn-max">Choisir Max</button>
        </div>
        """, unsafe_allow_html=True)

    # --- GESTION DES BOUTONS (simulation paiement) ---
    # Note : les boutons HTML sont décoratifs, on utilise des boutons Streamlit cachés pour l'action.
    # Pour éviter les doublons, on place deux vrais boutons Streamlit en bas de la colonne.
    # On peut aussi utiliser des callbacks JavaScript, mais plus simple ainsi.
    col_btn1, col_btn2, col_btn3 = st.columns(3)
    with col_btn2:
        if st.button("🚀 Passer à Pro (9,99€/mois)", key="pro_pay", use_container_width=True):
            st.balloons()
            st.success("🔜 Redirection vers le paiement sécurisé Stripe (mode test).")
        if st.button("👑 Passer à Max (24,99€/mois)", key="max_pay", use_container_width=True):
            st.balloons()
            st.success("🔜 Redirection vers le paiement sécurisé Stripe (mode test).")

    st.markdown("---")
    st.markdown("<p style='text-align: center; color: #6C6F78; font-size: 0.8rem;'>Paiement sécurisé par Stripe. Sans engagement, annulation à tout moment. Offre de lancement limitée.</p>", unsafe_allow_html=True)
# ============================================
# PAGE STATISTIQUES
# ============================================
elif menu == "📊 Stats":
    st.markdown("<h1 style='text-align: center;'>📊 Vos statistiques</h1>", unsafe_allow_html=True)

    if st.session_state.user is not None:
        user_id = st.session_state.user['localId']
        usage = get_usage(user_id)
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Messages envoyés", usage.get("messages", 0))
        with col2:
            st.metric("Images générées", usage.get("images", 0))
        with col3:
            days = (datetime.now() - usage.get("first_visit", datetime.now())).days
            st.metric("Jours d'utilisation", days)
        st.markdown("### 📈 Activité récente")
        st.bar_chart({"Lun": 5, "Mar": 8, "Mer": 12, "Jeu": 7, "Ven": 15, "Sam": 10, "Dim": 6})
    else:
        st.info("🔐 Connectez-vous pour voir vos statistiques personnelles.")
        st.bar_chart({"Lun": 0, "Mar": 0, "Mer": 0, "Jeu": 0, "Ven": 0, "Sam": 0, "Dim": 0})
# ===========================================
        #PAGE COMPARAISON
# ============================================
elif menu == "📊 Comparaison":
    comparison_html = """
    <!DOCTYPE html>
    <html lang="fr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>ANEYOND vs Concurrents</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                background: linear-gradient(135deg, #0A1929 0%, #1E3A8A 100%);
                font-family: 'Inter', system-ui, sans-serif;
                padding: 2rem;
                color: white;
            }
            .container { max-width: 1300px; margin: 0 auto; }
            h1 { text-align: center; font-size: 2.5rem; background: linear-gradient(135deg, #fff, #90CAF9); -webkit-background-clip: text; background-clip: text; color: transparent; margin-bottom: 0.5rem; }
            .subhead { text-align: center; color: #90CAF9; margin-bottom: 3rem; font-size: 1.2rem; }
            .comparison-table { background: rgba(10, 25, 41, 0.7); backdrop-filter: blur(8px); border-radius: 2rem; overflow: hidden; border: 1px solid rgba(59, 130, 246, 0.3); }
            table { width: 100%; border-collapse: collapse; }
            th, td { padding: 1.2rem 1rem; text-align: center; border-bottom: 1px solid rgba(59, 130, 246, 0.2); }
            th { background: rgba(30, 58, 138, 0.5); font-weight: 700; font-size: 1.2rem; color: #FFD700; }
            td:first-child, th:first-child { text-align: left; font-weight: 600; background: rgba(20, 30, 45, 0.6); position: sticky; left: 0; }
            .check { color: #4ade80; font-weight: bold; font-size: 1.3rem; }
            .cross { color: #f87171; font-weight: bold; font-size: 1.2rem; }
            .limited { color: #fbbf24; }
            .price { font-weight: 800; font-size: 1.4rem; color: #FFD700; }
            .badge { background: #3B82F6; display: inline-block; padding: 0.2rem 0.8rem; border-radius: 30px; font-size: 0.8rem; font-weight: 600; margin-top: 0.5rem; }
            .highlight { background: rgba(59, 130, 246, 0.2); border-left: 3px solid #3B82F6; }
            .cta { text-align: center; margin-top: 3rem; }
            .cta-button { background: linear-gradient(45deg, #1E3A8A, #3B82F6); border: none; padding: 1rem 2.5rem; border-radius: 50px; font-size: 1.2rem; font-weight: 700; color: white; cursor: pointer; transition: transform 0.2s; box-shadow: 0 4px 12px rgba(59,130,246,0.4); }
            .cta-button:hover { transform: translateY(-3px); box-shadow: 0 10px 25px rgba(59,130,246,0.5); }
            footer { text-align: center; margin-top: 3rem; color: #90CAF9; font-size: 0.8rem; }
            @media (max-width: 800px) { body { padding: 1rem; } th, td { padding: 0.8rem 0.5rem; font-size: 0.85rem; } .price { font-size: 1.1rem; } }
        </style>
    </head>
    <body>
    <div class="container">
        <h1>🚀 ANEYOND vs les géants de l'IA</h1>
        <div class="subhead">Pourquoi payer trois abonnements quand un seul fait tout ?</div>
        <div class="comparison-table">
            <table>
                <thead><tr><th>Fonctionnalité</th><th>🤖 ANEYOND</th><th>💬 ChatGPT (Plus)</th><th>🎨 Midjourney</th><th>🎬 Runway ML</th></tr></thead>
                <tbody>
                    <tr><td>💬 Chat intelligent</td><td class='check'>✓ Illimité (Premium)</td><td class='check'>✓ Oui</td><td class='cross'>✗ Non</td><td class='cross'>✗ Non</td></tr>
                    <tr><td>🎨 Génération d'images</td><td class='check'>✓ Oui (Flux, DALL‑E, etc.)</td><td class='check'>✓ Oui (DALL‑E)</td><td class='check'>✓ Excellence</td><td class='limited'>◐ Limitée</td></tr>
                    <tr class='highlight'><td>🎥 Vidéo longue (10‑30 min)</td><td class='check'>✓ <strong>Oui (bientôt)</strong><br><span class='badge'>Unique</span></td><td class='cross'>✗ Non</td><td class='cross'>✗ Non</td><td class='limited'>◐ Clips courts (<5s)</td></tr>
                    <tr><td>🔊 Voix / sous-titres auto</td><td class='check'>✓ Oui (vocal, TTS)</td><td class='limited'>◐ Uniquement app mobile</td><td class='cross'>✗ Non</td><td class='limited'>◐ Payant</td></tr>
                    <tr><td>📎 Upload fichiers & analyse</td><td class='check'>✓ Oui</td><td class='check'>✓ Oui</td><td class='cross'>✗ Non</td><td class='check'>✓ Oui (pro)</td></tr>
                    <tr><td>📜 Historique & sauvegarde cloud</td><td class='check'>✓ Oui (Firestore)</td><td class='check'>✓ Oui</td><td class='cross'>✗ Non</td><td class='limited'>◐ Projet limité</td></tr>
                    <tr><td>💰 Prix mensuel (Premium)</td><td class='price'>9,99€<br><span class='badge'>+ vidéo incluse</span></td><td class='price'>20€</td><td class='price'>10‑60€<br>(selon plan)</td><td class='price'>15‑95€</td></tr>
                    <tr><td>🔒 Données privées / souveraineté</td><td class='check'>✓ Option Europe</td><td class='limited'>◐ Formation possible</td><td class='check'>✓ Bon</td><td class='check'>✓ Bon</td></tr>
                    <tr><td>👨‍💻 Support humain</td><td class='check'>✓ Direct créateur</td><td class='cross'>✗ Automatique</td><td class='cross'>✗ Communauté</td><td class='limited'>◐ Ticket</td></tr>
                </tbody>
            </table>
        </div>
        <div class="cta">
            <button class="cta-button" onclick="window.location.href='/'">✨ Essayer ANEYOND gratuitement ✨</button>
            <p style="margin-top: 1rem; color:#90CAF9;">10 images/jour gratuites • 50 messages • 2 vidéos courtes</p>
        </div>
        <footer>ANEYOND – l’IA tout‑en‑un qui va plus loin que les géants.<br>Pas de multi‑abonnements, une seule interface pour chat, images et vidéos.</footer>
    </div>
    </body>
    </html>
    """
    st.components.v1.html(comparison_html, height=850, scrolling=True)


elif menu == "🌤️ Météo":
    st.markdown("<h1 style='text-align: center;'>🌤️ Météo Locale</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: #90CAF9;'>Prévisions pour votre emplacement</p>", unsafe_allow_html=True)

    # 1. Récupérer la localisation si elle n'existe pas encore
    if st.session_state.user_location is None:
        with st.spinner("📍 Détection de votre position..."):
            location = get_location_from_ip()
            if location:
                st.session_state.user_location = location
                # On vide les anciennes prévisions si la localisation change
                st.session_state.weather_forecast = None
            else:
                st.error("Impossible de déterminer votre localisation. Veuillez réessayer.")
                st.stop()

    # 2. Afficher la localisation
    loc = st.session_state.user_location
    st.markdown(f"**📍 Position détectée :** {loc['city']}, {loc['region']}, {loc['country']}")

    # 3. Récupérer la météo si elle n'existe pas encore ou si on force la mise à jour
    if st.button("🔄 Mettre à jour la météo"):
        st.session_state.weather_forecast = None
        st.rerun()

    if st.session_state.weather_forecast is None:
        with st.spinner("🌤️ Récupération des prévisions météo..."):
            forecast = get_weather_forecast(loc['lat'], loc['lon'])
            if forecast:
                st.session_state.weather_forecast = forecast
            else:
                st.error("Impossible de récupérer les prévisions météo.")
                st.stop()

    # 4. Afficher les prévisions
    forecast_data = st.session_state.weather_forecast
    daily = forecast_data['daily']
    
    st.markdown("---")
    st.subheader("📅 Prévisions sur 7 jours")
    
    # Création de colonnes pour un affichage côte à côte
    days = daily['time']
    temps_max = daily['temperature_2m_max']
    temps_min = daily['temperature_2m_min']
    codes_meteo = daily['weathercode']
    
    # Mapping simple des codes météo Open-Meteo vers des descriptions et emojis[reference:4]
    weather_codes = {
        0: ("☀️", "Ciel dégagé"), 1: ("🌤️", "Principalement dégagé"), 2: ("⛅", "Partiellement nuageux"),
        3: ("☁️", "Nuageux"), 45: ("🌫️", "Brouillard"), 51: ("🌧️", "Bruine légère"),
        61: ("🌦️", "Pluie légère"), 63: ("🌧️", "Pluie modérée"), 71: ("🌨️", "Neige légère"),
        80: ("☔", "Averses de pluie"),
    }
    
    # Utilisation des colonnes Streamlit pour un affichage compact[reference:5]
    cols = st.columns(len(days))
    for i, col in enumerate(cols):
        with col:
            code = codes_meteo[i]
            emoji, description = weather_codes.get(code, ("❓", "Inconnu"))
            st.metric(label=f"**{days[i]}**", value=f"{emoji} {temps_min[i]}° / {temps_max[i]}°")
            st.caption(description)


# ============================================
# PIED DE PAGE
# ============================================
st.markdown("---")
st.markdown("<p style='text-align: center; color: #90CAF9;'>🚀 ANEYOND - Beyond AI | © 2026</p>", unsafe_allow_html=True)
st.markdown("<p style='text-align: center; color: #90CAF9; font-size: 12px;'>7 jours d'essai • Sans engagement • Paiement sécurisé</p>", unsafe_allow_html=True)


