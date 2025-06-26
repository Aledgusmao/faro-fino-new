# Faro Fino - Versão Final e Estável
# Arquitetura simplificada para máxima robustez e estabilidade.

import os
import json
import logging
import asyncio
import httpx
from datetime import datetime, timedelta
import pytz
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode
from bs4 import BeautifulSoup
from email.utils import parsedate_to_datetime
import time
from urllib.parse import quote

# --- CONFIGURAÇÕES ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CONFIG_PATH = "faro_fino_config.json"
MONITORAMENTO_INTERVAL = 300
TIMEZONE_BR = pytz.timezone('America/Sao_Paulo')
DIAS_FILTRO_NOTICIAS = 3

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {"owner_id": None, "keywords": [], "monitoring_on": False, "history": set()}

# --- FUNÇÕES DE DADOS ---
def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)
            config['history'] = set(config.get('history', []))
            return config
    return DEFAULT_CONFIG.copy()

def save_config(config):
    to_save = config.copy()
    to_save['history'] = list(to_save.get('history', set()))
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(to_save, f, indent=4)
    logger.info("Configuração local salva.")

async def do_backup(context: ContextTypes.DEFAULT_TYPE):
    config = load_config()
    owner_id = config.get("owner_id")
    if not owner_id: return
    
    logger.info("Realizando backup no Telegram...")
    try:
        backup_data = {
            "keywords": config.get("keywords", []),
            "monitoring_on": config.get("monitoring_on", False),
        }
        json_data = json.dumps(backup_data, indent=4).encode('utf-8')
        timestamp = datetime.now(TIMEZONE_BR).strftime('%Y-%m-%d_%H-%M')
        filename = f"faro_fino_backup_{timestamp}.json"
        
        await context.bot.send_document(
            chat_id=owner_id, document=json_data, filename=filename,
            caption="Backup de segurança das palavras-chave e status de monitoramento."
        )
    except Exception as e:
        logger.error(f"Falha ao enviar backup: {e}")

# --- LÓGICA DE BUSCA ---
async def fetch_news(keywords: list) -> list:
    news_items = []
    if not keywords: return news_items
    
    # --- CORREÇÃO DEFINITIVA DO SYNTAXERROR ---
    query_parts = [f'"{k}"' for k in keywords]
    query = " OR ".join(query_parts)
    encoded_query = quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=pt-BR&gl=BR&ceid=BR:pt-419&tbs=qdr:h"
    # --- FIM DA CORREÇÃO ---

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=20)
        soup = BeautifulSoup(response.content, 'lxml-xml')
        for item in soup.find_all('item'):
            pub_date = parsedate_to_datetime(item.find('pubDate').text).astimezone(TIMEZONE_BR)
            news_items.append({'title': item.find('title').text, 'link': item.find('link').text, 'source': item.find('source').text, 'date': pub_date})
    except Exception as e:
        logger.error(f"Erro na busca: {e}")
    return news_items

async def process_news(context: ContextTypes.DEFAULT_TYPE, is_manual: bool = False):
    config = load_config()
    owner_id, keywords = config.get("owner_id"), config.get("keywords")
    if not owner_id or not keywords:
        if is_manual:
            await context.bot.send_message(chat_id=owner_id, text="Nenhuma palavra-chave configurada.")
        return

    if not config.get("monitoring_on") and not is_manual: return
    
    logger.info(f"Verificação {'manual' if is_manual else 'automática'} iniciada...")
    
    found_news = await fetch_news(keywords)
    new_articles = []
    date_limit = datetime.now(TIMEZONE_BR) - timedelta(days=DIAS_FILTRO_NOTICIAS)

    for article in found_news:
        if article['link'] in config['history'] or (article['date'] and article['date'] < date_limit): continue
        text_to_check = f"{article['title']} {article['source']}".lower()
        if any(k.lower() in text_to_check for k in keywords):
            article['found_keywords'] = [k for k in keywords if k.lower() in text_to_check]
            new_articles.append(article)
            config['history'].add(article['link'])
            
    if new_articles:
        await send_notifications(owner_id, new_articles, context)

    save_config(config)
    
    if not is_manual:
        ping_msg = f"_[Auto] Verificação concluída. {len(new_articles)} novas notícias._"
        await context.bot.send_message(chat_id=owner_id, text=ping_msg, parse_mode=ParseMode.MARKDOWN)

async def send_notifications(chat_id, articles, context: ContextTypes.DEFAULT_TYPE):
    for article in articles:
        date_str = article['date'].strftime('%d/%m/%Y %H:%M')
        message = (f"📰 *{article['title']}*\n\n"
                   f"🚨 *Encontrado:* {', '.join(article['found_keywords'])}\n"
                   f"📅 *Publicado em:* {date_str}\n"
                   f"🌐 *Fonte:* {article['source']}\n"
                   f"🔗 [Clique para ler]({article['link']})")
        await context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        await asyncio.sleep(1)

# --- LOOP DE MONITORAMENTO ---
async def monitor_loop(app: Application):
    context = ContextTypes.DEFAULT_TYPE(application=app)
    while True:
        await process_news(context)
        await asyncio.sleep(MONITORAMENTO_INTERVAL)

# --- COMANDOS ---
def is_owner(update: Update, config: dict) -> bool:
    return update.effective_user.id == config.get("owner_id")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = load_config()
    if not config.get('owner_id'):
        config['owner_id'] = update.effective_user.id
        save_config(config)
        await update.message.reply_text("Bem-vindo! Para restaurar um backup, encaminhe o arquivo .json para este chat.")
    else:
        await update.message.reply_text("Bem-vindo de volta!")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = load_config()
    if not is_owner(update, config): return
    await update.message.reply_text(
        "🤖 *Comandos:*\n\n"
        "🔹 `@palavra` - Adiciona palavras-chave\n"
        "🔹 `#palavra` - Remove palavras-chave\n\n"
        "/verificar - Busca notícias agora\n"
        "/status - Mostra o status e diagnóstico\n"
        "/monitoramento - Liga/desliga a busca automática\n"
        "/verpalavras - Lista as palavras-chave salvas\n"
        "/backup - Força um backup manual"
    )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = load_config()
    if not is_owner(update, config): return
    
    text = update.message.text.strip()
    if not text.startswith(('@', '#')): return

    keywords = set(config.get('keywords', []))
    items = [k.strip() for k in text[1:].split(',') if k.strip()]
    changed = False
    
    if text.startswith('@'):
        added = [k for k in items if k not in keywords]
        if added:
            keywords.update(added)
            changed = True
            msg = f"✅ Adicionados: {', '.join(added)}"
        else: msg = "ℹ️ Nenhum item novo adicionado."
    else: # starts with #
        removed = [k for k in items if k in keywords]
        if removed:
            keywords.difference_update(removed)
            changed = True
            msg = f"🗑️ Removidos: {', '.join(removed)}"
        else: msg = "ℹ️ Nenhum item removido."
        
    config['keywords'] = sorted(list(keywords))
    save_config(config)
    await update.message.reply_text(msg)
    
    if changed:
        await do_backup(context)

async def restore_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = load_config()
    if not is_owner(update, config) or not update.message.document: return
    
    document = update.message.document
    if "backup" in document.file_name and document.file_name.endswith('.json'):
        await update.message.reply_text("Processando backup...")
        try:
            file = await document.get_file()
            content = await file.download_as_bytearray()
            backup_data = json.loads(content)
            
            config['keywords'] = backup_data.get('keywords', [])
            config['monitoring_on'] = backup_data.get('monitoring_on', False)
            save_config(config)
            
            await update.message.reply_text("✅ Backup restaurado!")
        except Exception as e:
            await update.message.reply_text(f"🚨 Erro ao restaurar: {e}")

async def toggle_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = load_config()
    if not is_owner(update, config): return
    config['monitoring_on'] = not config.get('monitoring_on', False)
    save_config(config)
    await update.message.reply_text(f"Monitoramento {'ATIVADO' if config['monitoring_on'] else 'DESATIVADO'}.")

async def check_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Iniciando verificação manual...")
    await process_news(context, is_manual=True)
    await update.message.reply_text("Verificação manual concluída.")
    
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = load_config()
    if not is_owner(update, config): return
    
    await update.message.reply_text("Gerando status e diagnóstico...")
    
    status_text = (f"📊 *Status e Diagnóstico*\n\n"
                   f"∙ Monitoramento: {'🟢 Ativo' if config.get('monitoring_on') else '🔴 Inativo'}\n"
                   f"∙ Palavras-chave: {len(config.get('keywords', []))}\n"
                   f"∙ Histórico: {len(config.get('history', set()))} links")
                   
    keywords = config.get('keywords', [])
    if keywords:
        found_news = await fetch_news(keywords)
        if found_news:
            relevant_news = [n for n in found_news if any(k.lower() in f"{n['title']} {n['source']}".lower() for k in keywords)]
            success_rate = (len(relevant_news) / len(found_news)) * 100 if found_news else 0
            status_text += f"\n∙ Performance: {success_rate:.1f}% de taxa de sucesso"
    
    await update.message.reply_text(status_text, parse_mode=ParseMode.MARKDOWN)

async def view_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = load_config()
    if not is_owner(update, config): return
    keywords = config.get('keywords', [])
    await update.message.reply_text(f"📝 *Palavras-Chave:* {', '.join(keywords)}" if keywords else "Nenhuma.")

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Forçando backup manual...")
    await do_backup(context)

# --- FUNÇÃO PRINCIPAL ---
def main():
    if not BOT_TOKEN:
        logger.error("ERRO: BOT_TOKEN não configurado!")
        return

    application = Application.builder().token(BOT_TOKEN).build()

    async def post_init(app: Application):
        context = ContextTypes.DEFAULT_TYPE(application=app)
        asyncio.create_task(monitor_loop(context))
    application.post_init = post_init

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(CommandHandler('monitoramento', toggle_monitoring))
    application.add_handler(CommandHandler('verificar', check_now))
    application.add_handler(CommandHandler('status', status))
    application.add_handler(CommandHandler('verpalavras', view_keywords))
    application.add_handler(CommandHandler('backup', backup_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_handler(MessageHandler(filters.Document.FileExtension("json"), restore_handler))
    
    logger.info("🚀 Faro Fino Bot - Versão Final iniciado!")
    application.run_polling()

if __name__ == "__main__":
    main()
