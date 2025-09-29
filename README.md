# Auto-Fuzz
> Real-vehicle ECU fuzzing framework with multi-monitoring and automated recovery

## 🚀 개발 환경 세팅
### 1. 요구사항
* 운영체제 : Linux (Ubuntu 20.04+ 권장) 또는 WSL2
* Python : **3.10 이상 권장**
* Git
* CAN 인터페이스 드라이버

### 2. 레포지토리 클론
```bash
git clone https://github.com/Automotive-Fuzzing/auto-fuzz.git
cd auto-fuzz
```

### 3. 가상환경(venv) 생성 및 활성화
> 활성화되면 프롬프트에 `(.venv)`가 표시됩니다.
* Windows (CMD)
```bash
python -m venv .venv
.venv\Scripts\activate
```

* Windows (PowerShell)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

* macOS / Linux
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 4. pip 최신화
```bash
pip install --upgrade pip
```

### 5. 필수 라이브러리 설치
```bash
pip install -r requirements.txt
```

## ⚙️ Git 기본 규칙
> 기능 추가/수정은 반드시 브랜치 생성 후 Pull Request로 병합
코드 리뷰/테스트 통과 후 dev 브랜치에 반영

### 1. 기능 개발 시작: 브랜치 생성 및 이동
```bash
git switch -c feature/기능명
```

#### 🌿 브랜치명 규칙
* feature/기능명 (새 기능)
* bugfix/이슈번호-설명 (버그 수정)
* docs/문서명 (문서)
* test/설명 (테스트)
**Ex.** `feature/github-api-integration`, `bugfix/34-filter-extension-error`, `docs/update-readme`

### 2. 코드 수정 → 변경사항 저장 (commit)
```bash
git add "경로/파일이름.py"
git commit -m "Add: GitHub API 연동 기능 추가"
```
**Ex.** `Add: 퍼저 실행 기능 구현`, `Fix: DBC 파싱 시 버그 수정`, `Docs: README 업데이트`

#### 📝 커밋 메시지 규칙
* 타입 접두사 사용: `Add`, `Fix`, `Update`, `Remove`, `Refactor`, `Docs`, `Test`, `Chore`

### 3. GitHub에 업로드 (push)
```bash
git push origin feature/기능명
```

### 
