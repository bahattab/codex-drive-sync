# التثبيت على الجهازين

يتطلب Python 3.11 أو أحدث. اختُبر التطوير على Windows وPython 3.14.2؛ راجع تقرير الاختبارات لنتيجة تثبيت الحزمة الحالية. لا يشترط وجود Connections في إعدادات Codex.

افتح PowerShell في مجلد المصدر، وشغّل:

```powershell
.\scripts\install.ps1
& "$env:LOCALAPPDATA\Programs\codex-drive-sync\codex-drive-sync.cmd" doctor
```

لا تضف تشغيلًا تلقائيًا إلا إذا اخترته: `install.ps1 -EnableStartup` يسجل مهمة دخول للمستخدم. ترفض الخدمة العمل قبل إعداد الموفر ومراجعة أول جرد. التشغيل اليدوي: `service start`، والإيقاف: `service stop`، والفحص: `service health`. بعد توقف مفاجئ قد تبقى service-lease.json؛ تحقق من توقف العامل قبل إزالة ذلك الملف وحده. لا تمس engine/journal.json يدويًا.

اختبار النقل المحلي، بمجلد نقل مستقل عن المشاريع وبيانات الحالة:

```powershell
$sync = "$env:LOCALAPPDATA\Programs\codex-drive-sync\codex-drive-sync.cmd"
& $sync configure --provider local --transport C:\SyncTest\transport
& $sync projects add --id demo --path C:\SyncTest\projectA
& $sync sync --dry-run
& $sync sync --approve-inventory البصمة_المعروضة
& $sync status
```

مراجعة أول جرد تشمل جميع الملفات المختارة وأحجامها واستثناءاتها. لا تعتمد على أسماء الملفات وحدها لمعرفة احتوائها أسرارًا. يجيز قبول الجرد المزامنة المستمرة للمشاريع المختارة؛ إضافة مشروع أو تغيير الموفر يستدعي مراجعة جديدة. استخدم ملفات غير حساسة أولًا.

على الجهاز الثاني استورد مفتاح الاقتران محليًا **قبل** إنشاء مفتاح مستقل، ثم صل حساب Google نفسه وعميل OAuth نفسه، واربط `demo` بمسار مختلف عبر `projects map --id demo --path ...`. لا تنقل مجلد حالة الجهاز الأول إلى الثاني: يجب أن يبقى لكل جهاز معرّفه ومفاتيحه المحلية.

الإضافة في مجلد `plugin` بصيغة موثقة وبصيغة توافق. المثبت ينشئ نسخة محلية بربط MCP إلى Python المثبت بمسار مطلق. وجود الحزمة لا يثبت أنها مثبتة أو تعمل في واجهة Codex؛ اتبع إجراء marketplace المثبت في تقرير التوافق. أدواتها للقراءة فقط، ولا توجد خطافات تعدل قاعدة Codex.

بعد مراجعة الإضافة، أضف كتالوج التركيب المحلي: `codex plugin marketplace add "$env:LOCALAPPDATA\Programs\codex-drive-sync"` ثم `codex plugin add codex-drive-sync@codex-drive-sync-local` و`codex plugin list --json`. تحقق وجود الأمر في نسختك أولًا. قبول هذا الكتالوج والحزمة اختُبر في بيئات CLI معزولة، دون إثبات عمل داخل UI. لا تثبت نسخة المصدر ذات MCP بالاسم ما لم يكن codex-drive-sync متاحًا في PATH؛ النسخة التي يولدها المثبت تستخدم مسار Python المطلق.

الاستثناءات الإضافية: `codex-drive-sync exclusions --pattern '*.local.json' --pattern 'private/*'`. تضاف إلى القواعد الثابتة ولا تلغيها. الملفات المحلية المعروفة للعميل والاسترداد تُسجل خارج النقل ويُرفض تسجيل مجلد يشملها كمشروع أو موفر. لا يكشف ذلك أسرارًا مجهولة في ملفات أخرى. dry-run لا يعدّل ملفات المشروع ولا يرفع حمولة؛ قد ينشئ حالة ومفتاحًا محليين، وقد يقرأ كتالوج Drive لحساب خطة الاستقبال.

لتصدير مهارة اخترتها وإعدادات مشتركة محدودة ومخزون الإضافات:

```powershell
& $sync workspace-export --output C:\SyncTest\portable --skill my-skill
& $sync projects add --id portable --path C:\SyncTest\portable
& $sync sync --dry-run
```

انقل نسخ المهارات بعد مراجعتها إلى موضع مناسب على الجهاز المستقبِل. ادمج `shared-settings.json` يدويًا؛ ينقل فقط model وmodel_reasoning_effort وmodel_verbosity إن كانت قيمًا بسيطة، دون أسرار أو ثقة أو أذونات أو مسارات. مخزون الإضافات تعريفات فقط: تحقق من التثبيت والاعتماديات وربط الحساب وعمل الأدوات كلًا على حدة. المرفقات المختارة يمكن إضافتها كملفات مشروع، لكن لا تستعاد ارتباطاتها الداخلية بالمحادثات.

للإزالة:

```powershell
.\scripts\uninstall.ps1
```

توقف الخدمة ويزال تنفيذ البرنامج. المشاريع وحالة المزامنة واللقطات والنسخ الاحتياطية ومفاتيح نظام التشغيل محفوظة افتراضيًا. أزل الإضافة من Codex يدويًا بآليته المحلية؛ لا يعيد البرنامج كتابة إعداداته الأصلية. استخدم `disconnect --revoke` إذا أردت إلغاء التفويض قبل الإزالة.
