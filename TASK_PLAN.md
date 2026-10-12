# خطة codex-drive-sync

الهدف: استمرارية مساحة Codex بين جهازين عبر Google Drive، دون اعتبار أرشفة المحادثات استيرادًا أصليًا.

1. اكتشاف البيئة والتوافق، وفحص قابلية نقل المحادثة دون تعديل بيانات المستخدم.
2. محرك لقطات مشفرة ومزامنة ملفات بثلاثة أطراف، تعارضات ونسخ احتياطية وتعافٍ.
3. OAuth وGoogle Drive عبر appDataFolder؛ مفاتيح وبيانات تفويض محلية.
4. واجهة أوامر وخدمة وMCP وحزمة إضافة وأدلة عربية.
5. اختبارات سلوكية ومراجعة مستقلة وحزمة تثبيت وإزالة.
6. فحص خصوصية المصدر وتاريخه ثم رفع إلى GitHub بتصريح المستخدم.

ملكية الملفات: المدير يملك CLI والحزمة والتوثيق والتكامل. مختص المحرك يملك engine.py وtest_engine.py. مختص الموفر يملك providers.py وsecurity.py وtest_providers.py. مختص التوافق يملك work/compatibility-notes.md فقط. لا كتابة إلى حالة Codex الأصلية.

العقد: Python؛ المزود put(name, bytes), get(name)->bytes, list(prefix)->list[str]. أسماء الكائنات محدودة وآمنة. SecretStore.get/set/delete(name)، Cipher.encrypt/decrypt(bytes)، Cipher.from_store(store). المحرك Engine(state_dir, provider, cipher, device_id=None)؛ add_project(path, project_id=None)، map_project(project_id,path)، inventory()، sync(dry_run=False)، status()، conflicts()، resolve(conflict_id,choice)، snapshots()، restore(snapshot_id,dry_run=False)، prepare()، receive(snapshot_id,dry_run=False). جميع ملفات الحالة خارج جذور المشاريع. لا استيراد محادثات أصلية.

Google OAuth الحي والجهاز الثاني يحتاجان وصولًا وتفويضًا لم يتوفرا بعد. حالة الاختبار NOT RUN حتى التنفيذ الحقيقي.
