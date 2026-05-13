from docx import Document


def inspect_release_note_template():
    doc = Document("templates/RN_EG_CBE_Billing_Template.docx")

    print(f"Total tables = {len(doc.tables)}", flush=True)

    for i, table in enumerate(doc.tables):
        try:
            print(f"TABLE {i}: {table.cell(0, 0).text}", flush=True)
        except Exception as e:
            print(f"TABLE {i}: unable to read - {e}", flush=True)
