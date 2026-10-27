# Shaohu Zhang · Academic website

A static academic website with one publication file, a folder of captioned photos, and an everyday content editor.

## Everyday updates

1. Double-click **Update Website.command**. It opens Website Studio in your browser, normally at **http://127.0.0.1:8001/_editor**. Use the address it opens if another port is selected.
2. Choose **Publications**, **Photos**, or **Site content**, make your changes, and save. The editor rebuilds the website automatically.
3. Click **Preview website** to check your changes, then publish through GitHub as described below.

Keep the terminal window open while editing. Press **Control+C** in that window to stop the editor. Changes are saved to this folder; they reach the public site when you publish. Python 3.9 or newer is required.

To reopen Website Studio, double-click **Update Website.command**. If this website's editor is already running, the launcher reopens it. If its usual port is occupied by another application, it selects an available nearby port and opens the correct address.

If the editor was already running when its software was updated, stop it with **Control+C** and double-click **Update Website.command** again once to load the new version.

If Studio loses its connection, start the launcher again and click **Retry connection** in the editor. Unsaved form edits stay in the open tab. Open Studio through the launcher or its local `http://127.0.0.1:…/_editor` address; opening `tools/editor.html` directly cannot save changes.

**Rebuild** rereads saved content files. When the form has no unsaved changes, it also refreshes the form from those files; otherwise, Studio keeps your draft and labels it **Unsaved changes**.

## Publications: keep one record

In **Publications**, drop in a **BibTeX (`.bib`)** export from your reference manager, ORCID, or Google Scholar, then click **Import publications**. Choose **Add new publications** to retain the existing record and skip imported entries with matching citation keys. Choose **Replace the full record** when your export contains the complete bibliography.

The record is stored in `data/publications.bib`; you can also manage it directly in Zotero, JabRef, another reference manager, or a text editor. With the local editor running, saving that file automatically rebuilds the pages and refreshes an open local website preview. The homepage shows the six most recent publications and the lab page shows three, ordered by year; no `selected` flags are required. The full Publications page includes every record. Optional `pdf` and `slides` fields add document links; preserve them when replacing your record if you want to keep those links.

## Photos: drop an image, add a caption

In **Photos**, drop in JPG, PNG, or WebP images (up to 15 MB each), enter captions and optional dates, then click **Add photos to website**. Existing captions can be edited and saved directly in the gallery.

You can also drop images into `photos/` and add matching text files for captions:

```text
photos/2026-10-23-lab-meeting.jpg
photos/2026-10-23-lab-meeting.txt
```

The `.txt` file contains only the caption, such as `Our research group at the annual lab meeting.` A `YYYY-MM-DD-` filename prefix supplies the date. Captions are optional; the filename provides a fallback. The folder workflow also supports GIF files; GIF upload and caption editing are not available in the local editor. To remove a gallery item, remove its image and caption file, then rebuild.

The About page automatically shows the six newest gallery photos with their dates and captions. Click a photo to enlarge it, or **All photos** to open the full gallery.

## Students and headshots

The team roster and the new **X+ Intelligence Lab** page (`lab.html`) use the same student records. Manage names, roles, research interests, and starting terms under **Site content → Team** in the local editor. Leave unknown details blank.

Each student has a headshot placeholder. In **Site content → Team**, use the student's **Choose headshot** button to upload a photo; it updates both pages automatically. Save new or renamed students before uploading their photos. You can also drop a photo into `people/` with the student's filename, such as `people/charlestone-mayenga.jpg`, then click **Rebuild**. JPG, JPEG, PNG, and WebP are supported. [The headshot guide](people/README.md) lists all four student filenames and explains the optional `photo` field.

The lab page also draws its research directions, publications, and news from the existing content sources. Its introduction is managed in the `lab` section of `data/site.json` or **Site content → X+ Intelligence Lab**.

## Other content and direct file editing

Use **Site content** for your profile, Chinese and English names, biography, lab introduction, research, news, teaching, team, awards, service, and links, then click **Save site content**. Email addresses are managed locally and displayed on the public website as images.

Teaching, professional service, and awards appear on the **About** page (`index.html`). The separate Teaching page has been removed.

Courses, their teaching terms, service entries within each category, and awards display automatically from newest to oldest. Include a year in each dated entry, such as `Spring 2026` or `Committee, 2025–2026.`; month names and full `YYYY-MM-DD` dates are also supported. Entries with several dates use the latest one. Undated items appear afterward, and entries with equal dates keep their input order. You can add new entries anywhere in the editor or content file; service categories keep their existing order.

| To change | Source |
| --- | --- |
| Profile, Chinese and English names, and other text | `data/site.json` |
| Email addresses | Local editor; private settings in `.website-private/contacts.json` |
| Public email images | Automatically generated `assets/contact-primary.png` and `assets/contact-secondary.png` |
| Publication record | `data/publications.bib` |
| Gallery | Images and matching caption files in `photos/` |
| Student headshots | Images in `people/`; optional per-student `photo` path in `data/site.json` |
| Portrait or CV | The paths in `data/site.json` and the corresponding files |
| Design | `css/main.css` and `tools/build_site.py` |

The theme uses North Carolina A&T’s official blue (`#004684`) and gold (`#FDB927`), following the [university color palette](https://hub.ncat.edu/administration/chancellor/university-relations/branding/visual-identity/color-palette.php). Blue provides readable text on light backgrounds; gold accents frames, dividers, and details.

With the local editor running, changes to content files, gallery photos, and student headshots rebuild automatically. If an edit has an error, the previous preview stays available and the editor shows the problem. You can also click **Rebuild** or run:

```sh
python3 tools/build_site.py
```

This refreshes the public HTML pages. Edit the content sources above; manual changes to generated HTML are replaced by the next build.

## Publish with GitHub Pages

The redesigned website is ready locally. This folder has no Git connection, so these changes have **not been published**.

For automatic publishing, complete this setup once:

1. Upload the website source and public assets to the root of the GitHub repository that currently hosts your website. Include the root HTML and PDF files, `css/`, `pic/`, `publication/`, `data/`, `photos/`, `people/`, `tools/`, `assets/` (including both contact PNG images), and the hidden `.github/` folder containing `.github/workflows/pages.yml`. **Exclude `.website-private/`, `.website-backup/`, and `_site/`.** The `.gitignore` file excludes these folders when using Git; exclude them yourself when uploading through a browser.
2. In that repository, choose **Settings → Pages → Source → GitHub Actions**.
3. Commit to the repository's default branch. The **Build and publish website** workflow builds the pages from your content and deploys them. You can check its progress in **Actions**.

After setup, save locally, check the preview, and upload or commit your changed files to the default branch. Changes to `data/`, `photos/`, or `people/` automatically rebuild and publish the site; you do not need to edit or upload generated HTML separately. After changing an email address locally, include the updated contact PNG images under `assets/` in your upload or commit.

Your existing GitHub Pages branch publishing method also works: rebuild locally, then upload or commit the generated root HTML pages and their assets to your configured publishing branch. Keep `.website-private/` and `.website-backup/` out of the repository and public output.

## Email images and private settings

The editor stores email addresses only in `.website-private/contacts.json` on your computer. A local build converts them into the two contact PNG images. The public profile JSON, page HTML, image descriptions, and structured metadata contain no email-address text or `mailto:` links.

Keep a private copy of `.website-private/contacts.json` when moving the editor to another computer. Do not publish it. GitHub builds use the committed contact PNGs and work without that private file. To change an address, use the local editor, check the newly generated image in the preview, then publish the updated PNG along with your other changes.

## Original-site backup

The original HTML pages and stylesheet are saved in `.website-backup/original-pages.zip`. To restore the previous version, extract that archive into a separate folder and copy the files back into their original locations. Keep the backup until you are satisfied with the redesign.
