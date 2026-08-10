# WhatsApp templates to submit to Meta

Submit each in **WhatsApp Manager -> Templates -> Create template**.

Category **Utility** for all of them: it is the correct classification for
appointment and operational messages, and costs a fraction of Marketing.

Meta rejects a submission without a sample for each {{n}} placeholder.

---

## appointment_confirmation_en

- **Category:** Utility
- **Language:** English (`en`)
- **Sent to:** the customer

**Body:**

```
Hello {{1}}, your appointment at {{2}} is confirmed for {{3}}. Reply CANCEL to cancel or call {{4}} to reschedule.
```

**Sample values:**

- `{{1}}` = Anjali   (customer_name)
- `{{2}}` = Sunrise Clinic   (business_name)
- `{{3}}` = 12 Aug 2026, 4:30 PM   (appointment_time)
- `{{4}}` = +919810012345   (business_phone)

**Preview:**

> Hello Anjali, your appointment at Sunrise Clinic is confirmed for 12 Aug 2026, 4:30 PM. Reply CANCEL to cancel or call +919810012345 to reschedule.

---

## appointment_confirmation_hi

- **Category:** Utility
- **Language:** Hindi (`hi`)
- **Sent to:** the customer

**Body:**

```
नमस्ते {{1}}, {{2}} में आपका अपॉइंटमेंट {{3}} के लिए कन्फर्म हो गया है। रद्द करने के लिए CANCEL भेजें या {{4}} पर कॉल करें।
```

**Sample values:**

- `{{1}}` = Anjali   (customer_name)
- `{{2}}` = Sunrise Clinic   (business_name)
- `{{3}}` = 12 Aug 2026, 4:30 PM   (appointment_time)
- `{{4}}` = +919810012345   (business_phone)

**Preview:**

> नमस्ते Anjali, Sunrise Clinic में आपका अपॉइंटमेंट 12 Aug 2026, 4:30 PM के लिए कन्फर्म हो गया है। रद्द करने के लिए CANCEL भेजें या +919810012345 पर कॉल करें।

---

## appointment_reminder_24h_en

- **Category:** Utility
- **Language:** English (`en`)
- **Sent to:** the customer

**Body:**

```
Reminder: {{1}}, you have an appointment at {{2}} tomorrow at {{3}}. Reply CANCEL if you cannot make it.
```

**Sample values:**

- `{{1}}` = Anjali   (customer_name)
- `{{2}}` = Sunrise Clinic   (business_name)
- `{{3}}` = 12 Aug 2026, 4:30 PM   (appointment_time)

**Preview:**

> Reminder: Anjali, you have an appointment at Sunrise Clinic tomorrow at 12 Aug 2026, 4:30 PM. Reply CANCEL if you cannot make it.

---

## appointment_reminder_24h_hi

- **Category:** Utility
- **Language:** Hindi (`hi`)
- **Sent to:** the customer

**Body:**

```
याद दिलाने के लिए: {{1}}, कल {{3}} बजे {{2}} में आपका अपॉइंटमेंट है। अगर नहीं आ सकते तो CANCEL भेजें।
```

**Sample values:**

- `{{1}}` = Anjali   (customer_name)
- `{{2}}` = Sunrise Clinic   (business_name)
- `{{3}}` = 12 Aug 2026, 4:30 PM   (appointment_time)

**Preview:**

> याद दिलाने के लिए: Anjali, कल 12 Aug 2026, 4:30 PM बजे Sunrise Clinic में आपका अपॉइंटमेंट है। अगर नहीं आ सकते तो CANCEL भेजें।

---

## appointment_reminder_2h_en

- **Category:** Utility
- **Language:** English (`en`)
- **Sent to:** the customer

**Body:**

```
{{1}}, your appointment at {{2}} is in about 2 hours, at {{3}}. See you soon.
```

**Sample values:**

- `{{1}}` = Anjali   (customer_name)
- `{{2}}` = Sunrise Clinic   (business_name)
- `{{3}}` = 12 Aug 2026, 4:30 PM   (appointment_time)

**Preview:**

> Anjali, your appointment at Sunrise Clinic is in about 2 hours, at 12 Aug 2026, 4:30 PM. See you soon.

---

## appointment_reminder_2h_hi

- **Category:** Utility
- **Language:** Hindi (`hi`)
- **Sent to:** the customer

**Body:**

```
{{1}}, {{2}} में आपका अपॉइंटमेंट लगभग 2 घंटे में, {{3}} बजे है। जल्द मिलते हैं।
```

**Sample values:**

- `{{1}}` = Anjali   (customer_name)
- `{{2}}` = Sunrise Clinic   (business_name)
- `{{3}}` = 12 Aug 2026, 4:30 PM   (appointment_time)

**Preview:**

> Anjali, Sunrise Clinic में आपका अपॉइंटमेंट लगभग 2 घंटे में, 12 Aug 2026, 4:30 PM बजे है। जल्द मिलते हैं।

---

## appointment_cancelled_en

- **Category:** Utility
- **Language:** English (`en`)
- **Sent to:** the customer

**Body:**

```
Hello {{1}}, your appointment at {{2}} on {{3}} has been cancelled. Call {{4}} to book a new time.
```

**Sample values:**

- `{{1}}` = Anjali   (customer_name)
- `{{2}}` = Sunrise Clinic   (business_name)
- `{{3}}` = 12 Aug 2026, 4:30 PM   (appointment_time)
- `{{4}}` = +919810012345   (business_phone)

**Preview:**

> Hello Anjali, your appointment at Sunrise Clinic on 12 Aug 2026, 4:30 PM has been cancelled. Call +919810012345 to book a new time.

---

## appointment_cancelled_hi

- **Category:** Utility
- **Language:** Hindi (`hi`)
- **Sent to:** the customer

**Body:**

```
नमस्ते {{1}}, {{3}} को {{2}} में आपका अपॉइंटमेंट रद्द कर दिया गया है। नया समय बुक करने के लिए {{4}} पर कॉल करें।
```

**Sample values:**

- `{{1}}` = Anjali   (customer_name)
- `{{2}}` = Sunrise Clinic   (business_name)
- `{{3}}` = 12 Aug 2026, 4:30 PM   (appointment_time)
- `{{4}}` = +919810012345   (business_phone)

**Preview:**

> नमस्ते Anjali, 12 Aug 2026, 4:30 PM को Sunrise Clinic में आपका अपॉइंटमेंट रद्द कर दिया गया है। नया समय बुक करने के लिए +919810012345 पर कॉल करें।

---

## appointment_rescheduled_en

- **Category:** Utility
- **Language:** English (`en`)
- **Sent to:** the customer

**Body:**

```
Hello {{1}}, your appointment at {{2}} has been moved to {{3}}. Reply CANCEL if this does not work.
```

**Sample values:**

- `{{1}}` = Anjali   (customer_name)
- `{{2}}` = Sunrise Clinic   (business_name)
- `{{3}}` = 12 Aug 2026, 4:30 PM   (appointment_time)

**Preview:**

> Hello Anjali, your appointment at Sunrise Clinic has been moved to 12 Aug 2026, 4:30 PM. Reply CANCEL if this does not work.

---

## owner_booking_alert_en

- **Category:** Utility
- **Language:** English (`en`)
- **Sent to:** the business owner

**Body:**

```
New booking at {{1}}: {{2}} on {{3}}. Reason: {{4}}.
```

**Sample values:**

- `{{1}}` = Sunrise Clinic   (business_name)
- `{{2}}` = Anjali   (customer_name)
- `{{3}}` = 12 Aug 2026, 4:30 PM   (appointment_time)
- `{{4}}` = fever and cough   (service_reason)

**Preview:**

> New booking at Sunrise Clinic: Anjali on 12 Aug 2026, 4:30 PM. Reason: fever and cough.

---

## owner_daily_summary_en

- **Category:** Utility
- **Language:** English (`en`)
- **Sent to:** the business owner

**Body:**

```
{{1}} yesterday: {{2}} calls answered, {{3}} appointments booked, {{4}} cancelled. You have {{5}} appointments today.
```

**Sample values:**

- `{{1}}` = Sunrise Clinic   (business_name)
- `{{2}}` = 12   (calls_total)
- `{{3}}` = 8   (booked)
- `{{4}}` = 2   (cancelled)
- `{{5}}` = 5   (today_count)

**Preview:**

> Sunrise Clinic yesterday: 12 calls answered, 8 appointments booked, 2 cancelled. You have 5 appointments today.

---

## waitlist_slot_open_en

- **Category:** Utility
- **Language:** English (`en`)
- **Sent to:** the customer

**Body:**

```
Hello {{1}}, a slot has opened at {{2}} on {{3}}. Call {{4}} to take it before someone else does.
```

**Sample values:**

- `{{1}}` = Anjali   (customer_name)
- `{{2}}` = Sunrise Clinic   (business_name)
- `{{3}}` = 12 Aug 2026, 4:30 PM   (appointment_time)
- `{{4}}` = +919810012345   (business_phone)

**Preview:**

> Hello Anjali, a slot has opened at Sunrise Clinic on 12 Aug 2026, 4:30 PM. Call +919810012345 to take it before someone else does.

---

## waitlist_slot_open_hi

- **Category:** Utility
- **Language:** Hindi (`hi`)
- **Sent to:** the customer

**Body:**

```
नमस्ते {{1}}, {{2}} में {{3}} का slot खाली हो गया है। इसे लेने के लिए {{4}} पर कॉल करें।
```

**Sample values:**

- `{{1}}` = Anjali   (customer_name)
- `{{2}}` = Sunrise Clinic   (business_name)
- `{{3}}` = 12 Aug 2026, 4:30 PM   (appointment_time)
- `{{4}}` = +919810012345   (business_phone)

**Preview:**

> नमस्ते Anjali, Sunrise Clinic में 12 Aug 2026, 4:30 PM का slot खाली हो गया है। इसे लेने के लिए +919810012345 पर कॉल करें।

---

## appointment_rescheduled_hi

- **Category:** Utility
- **Language:** Hindi (`hi`)
- **Sent to:** the customer

**Body:**

```
नमस्ते {{1}}, {{2}} में आपका अपॉइंटमेंट {{3}} पर बदल दिया गया है। अगर यह ठीक नहीं है तो CANCEL भेजें।
```

**Sample values:**

- `{{1}}` = Anjali   (customer_name)
- `{{2}}` = Sunrise Clinic   (business_name)
- `{{3}}` = 12 Aug 2026, 4:30 PM   (appointment_time)

**Preview:**

> नमस्ते Anjali, Sunrise Clinic में आपका अपॉइंटमेंट 12 Aug 2026, 4:30 PM पर बदल दिया गया है। अगर यह ठीक नहीं है तो CANCEL भेजें।

---
