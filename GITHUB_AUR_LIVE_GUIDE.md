# GitHub par upload + Live website — step by step

> Customer ka naam/address wali PDF files (`samples/`, `*.pdf`) **GitHub par nahi jaati** — `.gitignore` unhe rok deta hai.

## Part 1 — Code GitHub par (5 minute)
1. Git install karein: https://git-scm.com/download/win (sab Next > Next).
2. https://github.com par login → **New repository**
   - Name: `flipkart-label-cropper`
   - **Private** select karein (recommended)
   - "Add README" tick **na** karein → **Create repository**
3. Jo URL dikhe (`https://github.com/USERNAME/flipkart-label-cropper.git`) copy karein.
4. `Crop` folder mein **`upload_to_github.bat`** par double-click → URL paste → Enter.
5. Browser mein GitHub login aayega → Sign in. Ho gaya — code GitHub par hai.

Baad mein code update karna ho to bas `upload_to_github.bat` dobara chala dein (wahi URL).

## Part 2 — Live website (internet se kahin bhi khule)
GitHub Pages sirf static HTML chala sakta hai; ye app Python server (PDF processing) hai,
isliye GitHub ke saath **Render.com (free)** use karein — repo me `Dockerfile` aur `render.yaml` ready hain.

1. https://render.com → **Sign in with GitHub**.
2. **New + → Blueprint** → apna `flipkart-label-cropper` repo select karein → **Apply**.
3. `PLC_ACCESS_PASSWORD` maangega → apna password likhein (website ka lock).
4. 5–10 minute build hoga → link milega jaise `https://pawan-flipkart-label-cropper.onrender.com`
5. Link kholein → Username: **pawan**, Password: jo aapne diya → app khul jayegi.

### Dhyan rakhein
- Free plan: 15 minute use na ho to server so jata hai; agli baar kholne par ~1 minute lagta hai.
- Live version me PDF Render ke server par process hoti hai (process ke baad delete). Sirf apne
  computer par rakhna ho to `run_windows.bat` hi kaafi hai — koi internet upload nahi.
- Password kabhi GitHub code me na likhein; sirf Render ke setting me.
