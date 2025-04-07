from dotenv import load_dotenv
load_dotenv()

from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
import os
import hashlib
import time
import json
import re
import uuid
import subprocess
import shutil
from pathlib import Path
from openai import OpenAI

app = Flask(__name__)
CORS(app)

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL")
)

app.config.update({
    'UPLOAD_FOLDER': os.path.abspath('./uploads'),
    'RESULTS_FOLDER': os.path.abspath('./results'),
    'METADATA_FILE': os.path.abspath('./file_metadata.json'),
    'CHAT_HISTORY': os.path.abspath('./chat_history'),
    'HOST': '0.0.0.0',
    'PORT': 5001,
    'DEBUG': True
})

class FileSystemManager:
    def __init__(self):
        self.upload_dir = Path(app.config['UPLOAD_FOLDER'])
        self.results_dir = Path(app.config['RESULTS_FOLDER'])
        self.metadata_file = Path(app.config['METADATA_FILE'])
        self.chat_dir = Path(app.config['CHAT_HISTORY'])
        
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.chat_dir.mkdir(parents=True, exist_ok=True)
        
        if not self.metadata_file.exists():
            self.metadata_file.write_text('[]', encoding='utf-8')

    def save_metadata(self, metadata):
        self.metadata_file.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding='utf-8')

    def load_metadata(self):
        try:
            return json.loads(self.metadata_file.read_text(encoding='utf-8'))
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def generate_file_id(self, filename):
        return f"{int(time.time())}_{hashlib.md5(filename.encode()).hexdigest()[:8]}"
    
    def get_content(self, file_id):
        metadata = self.load_metadata()
        if file_info := next((f for f in metadata if f['file_id'] == file_id), None):
            md_path = self.results_dir / file_info['result_files']['md']
            return md_path.read_text(encoding='utf-8')
        return None
    
    def get_chat_path(self, session_id):
        return self.chat_dir / f"{session_id}.json"
    
    def save_chat(self, session_id, history):
        chat_path = self.get_chat_path(session_id)
        chat_path.parent.mkdir(parents=True, exist_ok=True)
        chat_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding='utf-8')
    
    def load_chat(self, session_id):
        chat_path = self.get_chat_path(session_id)
        return json.loads(chat_path.read_text(encoding='utf-8')) if chat_path.exists() else []

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/files', methods=['GET'])
def get_file_list():
    try:
        fsm = FileSystemManager()
        return jsonify(success=True, files=fsm.load_metadata())
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500

@app.route('/api/upload', methods=['POST'])
def handle_upload():
    try:
        fsm = FileSystemManager()
        if 'pdfFile' not in request.files:
            return jsonify(success=False, error="未选择文件"), 400
            
        file = request.files['pdfFile']
        if not file or file.filename == '':
            return jsonify(success=False, error="空文件名"), 400

        if not file.filename.lower().endswith('.pdf'):
            return jsonify(success=False, error="仅支持PDF文件"), 400

        file_id = fsm.generate_file_id(file.filename)
        save_path = fsm.upload_dir / f"{file_id}.pdf"
        file.save(save_path)

        metadata = fsm.load_metadata()
        metadata.append({
            "file_id": file_id,
            "filename": file.filename,
            "upload_time": time.time(),
            "processed": False
        })
        fsm.save_metadata(metadata)

        return jsonify(success=True, file_id=file_id)
    except Exception as e:
        return jsonify(success=False, error=f"上传失败: {str(e)}"), 500

@app.route('/api/process/<file_id>', methods=['POST'])
def handle_process(file_id):
    try:
        fsm = FileSystemManager()
        metadata = fsm.load_metadata()
        
        file_index = next((i for i, f in enumerate(metadata) if f['file_id'] == file_id), None)
        if file_index is None:
            return jsonify(success=False, error="文件不存在"), 404

        file_record = metadata[file_index].copy()
        upload_path = fsm.upload_dir / f"{file_id}.pdf"
        
        try:  
            output_dir = fsm.results_dir / file_id
            #output_dir.mkdir(parents=True, exist_ok=True)
            
            cmd = f"magic-pdf -p {upload_path} -o {fsm.results_dir}"
            print(cmd)
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            if result.returncode != 0:
                raise Exception(f"转换失败: {result.stderr}")

            auto_dir = output_dir / "auto"
            md_path = auto_dir / f"{file_id}.md"
            
            if not md_path.exists():
                raise Exception("未找到生成的Markdown文件")

            file_record.update({
                'processed': True,
                'process_time': time.time(),
                'result_files': {
                    'md': f"{file_id}/auto/{file_id}.md",
                    'image_dir': f"{file_id}/auto/images"
                }
            })
            
            metadata[file_index] = file_record
            fsm.save_metadata(metadata)

            return jsonify(success=True)
        except Exception as e:  
            if output_dir.exists():
                shutil.rmtree(output_dir)
            raise e
    except Exception as e:  
        return jsonify(success=False, error=str(e)), 500

@app.route('/api/delete/<file_id>', methods=['DELETE'])
def handle_delete(file_id):
    try:
        fsm = FileSystemManager()
        metadata = fsm.load_metadata()
        
        # 查找文件记录
        if not (file_record := next((f for f in metadata if f['file_id'] == file_id), None)):
            return jsonify(success=False, error="文件不存在"), 404
            
        try:
            # 删除上传的PDF文件
            upload_file = fsm.upload_dir / f"{file_id}.pdf"
            upload_file.unlink(missing_ok=True)
            
            # 删除整个结果目录
            result_dir = fsm.results_dir / file_id
            if result_dir.exists():
                shutil.rmtree(result_dir)
                
            # 从元数据中移除记录
            new_metadata = [f for f in metadata if f['file_id'] != file_id]
            fsm.save_metadata(new_metadata)
            
            return jsonify(success=True)
        except Exception as e:
            return jsonify(success=False, error=f"删除过程中出错: {str(e)}"), 500
            
    except Exception as e:
        return jsonify(success=False, error=f"服务器错误: {str(e)}"), 500



@app.route('/results/<path:filename>')
def serve_result_file(filename):
    fsm = FileSystemManager()
    return send_from_directory(fsm.results_dir, filename)



@app.route('/api/chat/start', methods=['POST'])
def start_chat():
    try:
        fsm = FileSystemManager()
        file_id = request.json['file_id']
        
        if not (content := fsm.get_content(file_id)):
            return jsonify(success=False, error="文件未找到或未处理"), 404

        session_id = str(uuid.uuid4())
        system_message = {
            "role": "system",
            "content": f"基于以下文档内容回答问题：\n{content[:15000]}...\n（文档内容已截断，仅显示部分）"
        }
        
        fsm.save_chat(session_id, [system_message])
        return jsonify(success=True, 
                     session_id=session_id,
                     filename=Path(next(f for f in fsm.load_metadata() if f['file_id'] == file_id)['filename']).stem)
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500

@app.route('/api/chat/ask', methods=['POST'])
def chat_ask():
    try:
        fsm = FileSystemManager()
        data = request.json
        session_id = data['session_id']
        question = data['question']
        
        history = fsm.load_chat(session_id)        
        history.append({"role": "user", "content": question})
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=history,
            temperature=0.7,
            max_tokens=500
        )
        
        answer = response.choices[0].message.content
        history.append({"role": "assistant", "content": answer})
        fsm.save_chat(session_id, history)
        
        return jsonify(success=True, 
                     answer=answer,
                     tokens=response.usage.total_tokens)
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500

if __name__ == '__main__':
    app.run(host=app.config['HOST'], port=app.config['PORT'], debug=app.config['DEBUG'])