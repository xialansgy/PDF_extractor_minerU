# PDF_extractor_minerU

## 1. Install magic-pdf

```bash
conda create -n mineru python=3.10 -y
conda activate mineru
pip install -U "magic-pdf[full]"
python download_models_hf.py
pip install -r requirements.txt

```