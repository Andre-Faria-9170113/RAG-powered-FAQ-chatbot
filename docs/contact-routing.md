# Official contact routing

Source: [CustoJusto's public contact page](https://www.custojusto.pt/ajuda-contacto),
retrieved on 2026-10-21. The scraper reads that index and all ten category pages
using GET requests only; it does not submit forms or access customer data.

`data/contact_forms.json` records the source URL, UTC retrieval time, category
labels, section URLs and each published subject option's value and label. An
empty `subjects` list means the page has no subject dropdown, not that it is an
unusable destination. Option value `0` is a valid subject, not a placeholder.

| Internal key | Published category | Section |
|---|---|---|
| `problemas_anuncios` | Problemas com anúncios | 7 |
| `pagamentos_servicos` | Problemas com os serviços | 12 |
| `contactos_anuncios` | Contactos a anúncios | 6 |
| `problemas_tecnicos` | Problemas técnicos | 8 |
| `sugestoes` | Sugestões | 10 |
| `outros` | Outros | 11 |
| `imprensa_publicidade` | Imprensa e Publicidade | 14 |
| `versao_mobile` | Versão mobile | 15 |
| `acesso_telefones` | Dificuldade de acesso aos telefones | 16 |
| `conta_utilizador` | Problemas com a conta de utilizador | 17 |

The original six internal keys remain stable because their URLs were correct.
In particular, `pagamentos_servicos` is the existing compatible key for the
published services category, not an invented separate payments form.
`FAQResponse.relevant_contact_form` accepts these ten keys plus `none`.
Non-escalation evaluation cases keep `expected_contact_form=null`: having a
category does not by itself mean a user needs human support.

## Routing interpretation

Both validation and generation receive the pinned labels and published subjects.
The following precedence rules interpret those subjects; they are project policy,
not a claim that the website itself publishes an exhaustive routing algorithm:

- Inserting images is explicitly a technical-support subject, even though it
  occurs in an ad. An unresolved question about an ad's allowed image count is
  an ad-policy question, not an image-upload failure.
- Accessing advertisers' telephone numbers has a dedicated destination. It is
  distinct from recovering an account or a missing chat message.
- Institutional press/advertising is distinct from publishing a classified ad
  or buying its paid promotion; paid promotions and invoices belong to services.
- Mobile-specific interface problems use the mobile destination unless a more
  specific published subject describes the problem.
- Feature suggestions have their own destination. `outros` is a fallback, for
  example an unresolved complaint about support rather than a product suggestion.

The existing low-confidence/no-missing-information escalation guard is unchanged.
The app appends the official label and absolute HTTPS URL; it never submits the
form. Subject option values are not assumed to be working URL parameters, so
links select only the verified section, not an invented preselected subject.

## Refresh and review

```bash
.venv/bin/python scrape_contact_forms.py
.venv/bin/python -c 'from contact_forms import CONTACT_FORMS_VERSION; print(CONTACT_FORMS_VERSION)'
```

Scraping is an explicit maintenance command, never part of answering a question.
A changed section list or mismatched heading aborts the refresh before writing
the snapshot. If the site's category structure changes, review the scraper's
section/key mapping and the shared `ContactFormKey` response schema together.

After a content change, review the routing policy and every evaluation case,
update `contact_forms_version` in the dataset, increment its version and retain
draft status until a named human reviews it. The content hash excludes retrieval
time, so refreshing an unchanged catalog does not invalidate labels. Old runs
keep their original dataset hashes and scores; see [evaluation](evaluation.md).
