from docx import Document

doc = Document("templates/RN_EG_CBE_Billing_Template.docx")

print(f"Total tables = {len(doc.tables)}")

for i, table in enumerate(doc.tables):
    try:
        print(f"TABLE {i}: {table.cell(0,0).text}")
    except:
        print(f"TABLE {i}: unable to read")
