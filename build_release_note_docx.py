from docx import Document

doc = Document("RN_EG_CBE_Billing_Template.docx")

print(len(doc.tables))

for i, table in enumerate(doc.tables):
    print(i, table.cell(0,0).text)
