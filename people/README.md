# Student headshots

Every student has a headshot placeholder. The easiest way to add a photo is **Site content → Team → Choose headshot** in the local editor. Save new or renamed students before uploading their photos. The team and lab pages update automatically.

You can also drop the image into this folder with the matching filename, then click **Rebuild** in the local editor or run `python3 tools/build_site.py` from the website folder.

| Student | Suggested filename |
| --- | --- |
| Md Mehraz Hosen | `md-mehraz-hosen.jpg` |
| Bontu Geremew | `bontu-geremew.jpg` |
| Sharun Akter Khushbu | `sharun-akter-khushbu.jpg` |
| Charlestone Mayenga | `charlestone-mayenga.jpg` |

The automatic lookup also accepts `.jpeg`, `.png`, and `.webp`. Use one image per student. A square portrait with the face centered works well; the website crops it to fit the card.

Student names, roles, research interests, and starting terms are managed under **Site content → Team** in the local editor or in `data/site.json`. Each student's optional `photo` field can specify another relative image path; leave it blank to use the filename lookup above. Without a matching image, the website uses `assets/headshot-placeholder.svg`.

The team and lab pages use the same student records, so a photo or name update appears in both places after rebuilding. Include new headshot files in your next GitHub upload or commit.
