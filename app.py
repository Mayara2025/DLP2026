import csv
import smtplib
import ssl
import io
import uuid
import json
import os
import time
import random
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from flask import Flask, request, render_template, flash, redirect, url_for, Response, send_from_directory

# --- 1. CONFIGURAÇÕES ---
app = Flask(__name__)
app.secret_key = 'uma-chave-secreta-muito-segura-e-diferente'
tasks = {}

# As credenciais de e-mail foram removidas daqui para serem inseridas na interface.
SMTP_SERVER = 'smtp.gmail.com'
SMTP_PORT = 587
REMETENTE_NOME = 'REMETENTE'

# Template HTML que será passado para a página web
HTML_INICIAL = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <title>Oportunidade de Otimização</title>
</head>
<body>
    <h2>Olá, {nome_contato},</h2>
    <p>
        Sei que a rotina em um escritório como a <strong>{nome_escritorio}</strong> é marcada por um grande volume de tarefas.
    </p>
</body>
</html>
"""

# --- 2. LÓGICA DE ENVIO DE E-MAIL ---
def stream_send_emails(task_id):
    """
    Processa a tarefa de envio e transmite o status de cada e-mail via yield.
    """
    task = tasks.get(task_id)
    if not task:
        yield f"data: {json.dumps({'type': 'error', 'message': 'Tarefa não encontrada.'})}\n\n"
        return

    csv_content = task['csv_content']
    html_template = task['html_template']
    smtp_user = task['smtp_user']
    smtp_password = task['smtp_password']
    
    success_count = 0
    failure_count = 0
    
    try:
        reader = list(csv.DictReader(io.StringIO(csv_content)))
        total_emails = len(reader)
        
        email_list = [{'id': i, 'email': row.get('E-mail do Sócio', 'N/A'), 'escritorio': row.get('Nome do Escritório', 'N/A'), 'status': 'Pendente'} for i, row in enumerate(reader)]
        yield f"data: {json.dumps({'type': 'setup', 'emails': email_list})}\n\n"

        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=60) as server:
            server.starttls(context=context)
            server.login(smtp_user, smtp_password)

            for i, row in enumerate(reader):
                email_socio = row.get('E-mail do Sócio', '').strip()
                nome_socio = row.get('Nome do Sócio', '').strip()
                nome_escritorio = row.get('Nome do Escritório', '').strip()
                
                if not email_socio:
                    failure_count += 1
                    status_data = {'id': i, 'status': 'falha', 'message': 'E-mail não encontrado no CSV.'}
                    yield f"data: {json.dumps({'type': 'update', 'data': status_data})}\n\n"
                else:
                    try:
                        safe_html_template = html_template.replace('{', '{{').replace('}', '}}')
                        safe_html_template = safe_html_template.replace('{{nome_contato}}', '{nome_contato}')
                        safe_html_template = safe_html_template.replace('{{nome_escritorio}}', '{nome_escritorio}')
                        html_body = safe_html_template.format(nome_contato=nome_socio, nome_escritorio=nome_escritorio)

                        msg = MIMEMultipart('alternative')
                        msg['Subject'] = Header(f"Menos digitação, mais estratégia para {nome_escritorio}", 'utf-8')
                        msg['From'] = formataddr((REMETENTE_NOME, smtp_user))
                        msg['To'] = formataddr((nome_socio, email_socio))
                        msg.attach(MIMEText(html_body, 'html', 'utf-8'))
                        server.sendmail(smtp_user, email_socio, msg.as_string())
                        
                        success_count += 1
                        status_data = {'id': i, 'status': 'sucesso'}
                        yield f"data: {json.dumps({'type': 'update', 'data': status_data})}\n\n"

                    except Exception as e:
                        failure_count += 1
                        error_message = str(e)
                        if isinstance(e, smtplib.SMTPRecipientsRefused):
                            error_message = f"Destinatário recusado: {email_socio}"
                        
                        status_data = {'id': i, 'status': 'falha', 'message': error_message}
                        yield f"data: {json.dumps({'type': 'update', 'data': status_data})}\n\n"
                
                # Envia o progresso e os contadores atualizados
                yield f"data: {json.dumps({'type': 'progress', 'sent': i + 1, 'total': total_emails, 'success': success_count, 'failure': failure_count})}\n\n"
                
                if i < total_emails - 1:
                    delay = random.uniform(10, 25)
                    yield f"data: {json.dumps({'type': 'waiting', 'delay': round(delay, 1)})}\n\n"
                    time.sleep(delay)
        
    except smtplib.SMTPAuthenticationError:
        yield f"data: {json.dumps({'type': 'error', 'message': 'Erro de Autenticação. Verifique seu e-mail e senha de aplicativo.'})}\n\n"
        return
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': f'Erro geral no processo: {e}'})}\n\n"
        return
    finally:
        yield f"data: {json.dumps({'type': 'done', 'message': 'Processo finalizado.'})}\n\n"
        if task_id in tasks:
            del tasks[task_id]


# --- 3. ROTAS DA APLICAÇÃO WEB ---
@app.route('/')
def index():
    return render_template('index.html', html_inicial=HTML_INICIAL)

@app.route('/enviar', methods=['POST'])
def handle_envio():
    if 'csv_file' not in request.files or not request.files['csv_file'].filename:
        flash('Nenhum arquivo CSV foi selecionado.', 'danger')
        return redirect(url_for('index'))

    file = request.files['csv_file']
    html_template = request.form.get('html_template')
    smtp_user = request.form.get('smtp_user')
    smtp_password = request.form.get('smtp_password')

    if not all([smtp_user, smtp_password]):
        flash('Por favor, preencha o e-mail e a senha de aplicativo.', 'danger')
        return redirect(url_for('index'))

    if not file.filename.endswith('.csv'):
        flash('Formato de arquivo inválido. Por favor, envie um arquivo .csv.', 'danger')
        return redirect(url_for('index'))

    task_id = str(uuid.uuid4())
    tasks[task_id] = {
        'csv_content': file.stream.read().decode('utf-8-sig'),
        'html_template': html_template,
        'smtp_user': smtp_user,
        'smtp_password': smtp_password
    }
    
    return redirect(url_for('status_page', task_id=task_id))

@app.route('/status/<task_id>')
def status_page(task_id):
    return render_template('status.html', task_id=task_id)

@app.route('/stream-status/<task_id>')
def stream_status(task_id):
    return Response(stream_send_emails(task_id), mimetype='text/event-stream')

# A rota de download de relatório foi removida.

# --- 4. EXECUÇÃO DO SERVIDOR ---
if __name__ == '__main__':
    templates_dir = 'templates'
    if not os.path.exists(templates_dir):
        os.makedirs(templates_dir)

    index_html_content = """<!DOCTYPE html><html lang="pt-br"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Disparador de E-mails | ACSL Consultoria</title><script src="https://cdn.tailwindcss.com"></script><script>tailwind.config = { theme: { extend: { colors: { 'acsl-blue': '#002060', 'acsl-orange': '#FFC000' } } } }</script></head><body class="bg-gray-100 font-sans"><div class="container mx-auto mt-10 max-w-7xl p-8 bg-white rounded-lg shadow-xl border-t-8 border-acsl-blue"><h1 class="text-3xl font-bold text-acsl-blue mb-6">Disparador de E-mails em Massa</h1>{% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="p-4 mb-4 text-sm rounded-lg {% if category == 'danger' %} bg-red-100 text-red-800 {% endif %}" role="alert"><span class="font-medium">Erro:</span> {{ message }}</div>{% endfor %}{% endif %}{% endwith %}<div class="grid grid-cols-1 md:grid-cols-2 gap-8"><div><form action="/enviar" method="post" enctype="multipart/form-data"><div class="bg-gray-50 p-4 rounded-lg border border-gray-200 mb-6"><h3 class="text-lg font-semibold text-acsl-blue mb-4">1. Configurações de Envio</h3><div class="mb-4"> <label for="smtp_user" class="block mb-2 text-sm font-medium text-gray-700">Seu E-mail (Remetente)</label> <input type="email" name="smtp_user" id="smtp_user" required class="bg-white border border-gray-300 text-gray-900 text-sm rounded-lg focus:ring-acsl-orange focus:border-acsl-orange block w-full p-2.5" placeholder="seu.email@gmail.com"></div><div> <label for="smtp_password" class="block mb-2 text-sm font-medium text-gray-700">Sua Senha de Aplicativo</label> <input type="password" name="smtp_password" id="smtp_password" required class="bg-white border border-gray-300 text-gray-900 text-sm rounded-lg focus:ring-acsl-orange focus:border-acsl-orange block w-full p-2.5" placeholder="••••••••••••••••"></div></div><div class="mb-4"><label for="csv_file" class="block mb-2 text-sm font-medium text-gray-700">2. Selecione o arquivo .CSV</label><input type="file" name="csv_file" id="csv_file" required class="block w-full text-sm text-gray-900 bg-gray-50 rounded-lg border border-gray-300 cursor-pointer focus:outline-none focus:ring-2 focus:ring-acsl-orange"></div><h3 class="text-lg font-semibold text-acsl-blue mt-6 mb-2">Dados para Pré-visualização</h3><div class="grid grid-cols-2 gap-4 mb-4"><div><label for="exemplo_nome" class="block mb-2 text-sm font-medium text-gray-700">Nome (Exemplo)</label><input type="text" id="exemplo_nome" value="Fulano de Tal" class="bg-gray-50 border border-gray-300 text-gray-900 text-sm rounded-lg focus:ring-acsl-orange focus:border-acsl-orange block w-full p-2.5"></div><div><label for="exemplo_escritorio" class="block mb-2 text-sm font-medium text-gray-700">Escritório (Exemplo)</label><input type="text" id="exemplo_escritorio" value="Contabilidade Exemplo" class="bg-gray-50 border border-gray-300 text-gray-900 text-sm rounded-lg focus:ring-acsl-orange focus:border-acsl-orange block w-full p-2.5"></div></div><div class="mb-6"><label for="html_template" class="block mb-2 text-sm font-medium text-gray-700">3. Cole o HTML do e-mail</label><textarea name="html_template" id="html_template" rows="12" class="block p-2.5 w-full text-sm text-gray-900 bg-gray-50 rounded-lg border border-gray-300 focus:ring-2 focus:ring-acsl-orange focus:border-acsl-orange">{{ html_inicial|safe }}</textarea></div><button type="submit" class="w-full text-acsl-blue bg-acsl-orange hover:bg-amber-500 focus:ring-4 focus:outline-none focus:ring-amber-300 font-bold rounded-lg text-base px-5 py-3 text-center transition-colors duration-300">Iniciar Envio e Ver Progresso</button></form></div><div><label class="block mb-2 text-sm font-medium text-gray-700">Pré-visualização do E-mail</label><div class="w-full h-full border border-gray-300 rounded-lg bg-white overflow-hidden"><iframe id="preview-iframe" class="w-full h-full" style="min-height: 600px;"></iframe></div></div></div></div><script>const htmlTemplateTextarea = document.getElementById('html_template');const exemploNomeInput = document.getElementById('exemplo_nome');const exemploEscritorioInput = document.getElementById('exemplo_escritorio');const previewIframe = document.getElementById('preview-iframe');function updatePreview() {let htmlContent = htmlTemplateTextarea.value;const nomeExemplo = exemploNomeInput.value || '[Nome do Contato]';const escritorioExemplo = exemploEscritorioInput.value || '[Nome do Escritório]';htmlContent = htmlContent.replace(/{nome_contato}/g, nomeExemplo).replace(/{nome_escritorio}/g, escritorioExemplo);const iframeDoc = previewIframe.contentWindow.document;iframeDoc.open();iframeDoc.write(htmlContent);iframeDoc.close();}htmlTemplateTextarea.addEventListener('input', updatePreview);exemploNomeInput.addEventListener('input', updatePreview);exemploEscritorioInput.addEventListener('input', updatePreview);window.addEventListener('load', updatePreview);</script></body></html>"""
    status_html_content = """<!DOCTYPE html><html lang="pt-br"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Status de Envio | ACSL Consultoria</title><script src="https://cdn.tailwindcss.com"></script><script>tailwind.config = { theme: { extend: { colors: { 'acsl-blue': '#002060', 'acsl-orange': '#FFC000' } } } }</script></head><body class="bg-gray-100 font-sans"><div class="container mx-auto mt-10 max-w-4xl p-8 bg-white rounded-lg shadow-xl border-t-8 border-acsl-blue"><h1 class="text-3xl font-bold text-acsl-blue mb-2">Progresso de Envio</h1><p id="geral-status" class="text-gray-600 mb-4">Iniciando conexão com o servidor...</p><div class="flex justify-center space-x-8 my-4 p-4 bg-gray-50 rounded-lg"><div class="text-center"><p class="text-2xl font-bold text-green-600" id="success-count">0</p><p class="text-sm text-gray-500">Sucessos</p></div><div class="text-center"><p class="text-2xl font-bold text-red-600" id="failure-count">0</p><p class="text-sm text-gray-500">Falhas</p></div></div><div class="overflow-x-auto"><table class="min-w-full bg-white"><thead class="bg-gray-50"><tr><th class="py-3 px-6 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Escritório</th><th class="py-3 px-6 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">E-mail</th><th class="py-3 px-6 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th></tr></thead><tbody id="status-grid" class="bg-white divide-y divide-gray-200"></tbody></table></div><div class="mt-6 text-center"><a href="/" class="text-white bg-acsl-blue hover:bg-blue-900 font-bold rounded-lg text-sm px-5 py-3 transition-colors duration-300">Voltar para o Início</a></div></div><script>const task_id = "{{ task_id }}";const source = new EventSource(`/stream-status/${task_id}`);const statusGrid = document.getElementById('status-grid');const geralStatus = document.getElementById('geral-status');const successCountEl = document.getElementById('success-count');const failureCountEl = document.getElementById('failure-count');let totalCount = 0;let sentCount = 0;source.onmessage = function(event) {const data = JSON.parse(event.data);if (data.type === 'setup') {totalCount = data.emails.length; geralStatus.textContent = `Preparando para enviar ${totalCount} e-mails...`;data.emails.forEach(email => {const row = document.createElement('tr');row.id = 'email-' + email.id;row.innerHTML = `<td class="py-4 px-6 text-sm text-gray-900">${email.escritorio}</td><td class="py-4 px-6 text-sm text-gray-500">${email.email}</td><td class="py-4 px-6 text-sm"><span class="status-badge bg-gray-200 text-gray-800 text-xs font-medium mr-2 px-2.5 py-0.5 rounded-full">${email.status}</span><p class="error-message text-red-600 text-xs mt-1"></p></td>`;statusGrid.appendChild(row);});} else if (data.type === 'update') {const row = document.getElementById('email-' + data.data.id);if (row) {const statusBadge = row.querySelector('.status-badge'); const errorMessage = row.querySelector('.error-message'); if (data.data.status === 'sucesso') {statusBadge.textContent = 'Enviado';statusBadge.className = 'status-badge bg-green-100 text-green-800 text-xs font-medium mr-2 px-2.5 py-0.5 rounded-full';errorMessage.textContent = '';} else {statusBadge.textContent = 'Falha';statusBadge.className = 'status-badge bg-red-100 text-red-800 text-xs font-medium mr-2 px-2.5 py-0.5 rounded-full';errorMessage.textContent = data.data.message;}}} else if (data.type === 'progress') { sentCount = data.sent; successCountEl.textContent = data.success; failureCountEl.textContent = data.failure; geralStatus.textContent = `Enviando... (${sentCount}/${totalCount})`; } else if (data.type === 'waiting') { geralStatus.textContent = `Aguardando ${data.delay} segundos... (${sentCount}/${totalCount})`; } else if (data.type === 'done') {geralStatus.textContent = data.message; source.close();} else if (data.type === 'error') {geralStatus.textContent = "Erro: " + data.message;geralStatus.style.color = 'red';source.close();}};source.onerror = function(err) {geralStatus.textContent = 'Conexão com o servidor perdida. O processo pode ter sido interrompido.';geralStatus.style.color = 'red';source.close();};</script></body></html>"""
    
    with open(os.path.join(templates_dir, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(index_html_content)
    
    with open(os.path.join(templates_dir, 'status.html'), 'w', encoding='utf-8') as f:
        f.write(status_html_content)
        
    print("Iniciando a aplicação Flask. Acesse http://127.0.0.1:5001 no seu navegador.")
    app.run(debug=True, host='0.0.0.0', port=5001)
