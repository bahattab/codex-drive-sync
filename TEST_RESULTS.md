# نتائج الاختبار — 2026-10-12

البيئة: Windows، Python 3.14.2. أحدث تشغيل: **68 اختبارًا PASS** في 11.739 ثانية، بالأمر `.venv\Scripts\python.exe -m unittest discover -s tests -q`، exit 0. لا فشل أو skipped. الجولة المستقلة السابقة 62 PASS؛ أضيفت بعدها اختبارات الخصوصية وأسماء Drive المتكررة.

| الاختبار والدليل | الحالة |
|---|---|
| إنشاء/تعديل/حذف/استعادة، اختلاف المسارات، تعارض التحرير والحذف، سلالات دون اتصال، فشل النقل قبل manifest، فساد حمولة ثابتة ونسخة تعارض، استعادة متعددة الملفات وانقطاع التطبيق | PASS؛ tests/test_engine.py |
| ملفات عربية وثنائية وحدود الأحجام وعدم التكرار، DAG سببي دون الساعة، schema مجهول، traversal وcase collision وروابط، استثناءات وتشفير AES-GCM الحقيقي مع LocalProvider | PASS؛ اختبارات المحرك |
| المفتاح الخاطئ وnonce مختلف، duplicate Drive موثق بالبصمة، محتوى/مفتاح/بصمة مغاير، OS backend غير آمن، OAuth state/PKCE/timeout، انتهاء access token ورفض الإذن وretries وتنزيل محدود | PASS؛ tests/test_providers.py؛ طلبات الشبكة هنا محاكاة |
| مخزن Windows الحقيقي، set/get/delete اصطناعي في namespace فريد وتنظيفه | PASS؛ keyring.backends.Windows |
| حماية مسارات الاعتماد الحالية والمستقبلية، إعدادات وثقة وأسرار مستبعدة، archive integrity/search، سجل بلا نص استثناء حساس، MCP schema | PASS؛ tests/test_interfaces.py |
| PID الحقيقي وlease/token، stop timeout، stop أثناء initialization، cleanup وسجل منقح | PASS؛ tests/test_service.py؛ 8 اختبارات مستقلة |
| Google Desktop OAuth الحقيقي، state/PKCE ومتصفح وموافقة المستخدم | PASS؛ connect exit 0 وconnected=true وscope drive.appdata؛ دون تسجيل أسرار |
| Google Drive الحقيقي: نقل مشفر وreceive وملف عربي وثنائي وعودة وعدم تكرار snapshot | PASS؛ scripts/verify_drive.py exit 0؛ بيئتان محليتان مؤقتتان، لا جهازان |
| تثبيت Windows في وجهة مستقلة من lockfile وwheel | PASS؛ scripts/install.ps1 exit 0 |
| CLI sync والخدمة الحقيقية start/health/stop وstdio MCP من حزمة مثبتة وبقاء المشروع | PASS؛ scripts/verify_install.py exit 0؛ مفتاح اصطناعي نظف |
| إزالة Windows مع توقف مؤكد، venv_removed=true وproject_preserved=true | PASS؛ scripts/uninstall.ps1 exit 0 |
| قبول وتثبيت وتفعيل الإضافة بالكتالوج في CLI0.146.0 وruntime0.162.0-alpha.17.2 | PASS؛ marketplace add/plugin add/plugin list في CODEX_HOME مؤقت؛ مراجعة مستقلة |
| الإضافة عبر واجهة Codex Desktop | NOT RUN؛ stdio وCLI لا يثبتان UI |
| Google concurrent races/quota وسحب تفويض حساب حقيقي | NOT RUN؛ محاكاة منفصلة |
| OS startup Scheduled Task | NOT RUN؛ لم يختَر المستخدم تفعيله |
| اللابتوب الحقيقي وعودة الاختبار | NOT RUN؛ تحدٍ وإجراء معدان بانتظار B |
| استيراد أصلي للمحادثة وUI continuation مع أدوات ومرفقات وربط مشروع | NOT RUN / BLOCKED؛ لا adapter موثوق |

أصلحت عيوب المراجعة المستقلة وأضيفت regressions. نفذ المسؤول اختبارات الحساب والتركيب الحية؛ لم يدّع المراجع إجراء موافقة الحساب. النتائج ليست شهادة مزامنة Codex كاملة.
