<?php
/* This file is part of Jeedom.
 *
 * Jeedom is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * Jeedom is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with Jeedom. If not, see <http://www.gnu.org/licenses/>.
 */

/*
 * Page principale de JeeTerm : un terminal, et rien d'autre.
 *
 * Le plugin n'a pas d'équipement : pas de plugin.template.js, pas de liste de
 * cartes. Tout le travail est fait par desktop/js/jeeterm.js, qui demande un
 * jeton à core/ajax/jeeterm.ajax.php puis ouvre le WebSocket vers le démon
 * (voir CONCEPTION.md). Ce fichier ne pose que la structure.
 *
 * Les boutons sont des <button> et non des <a> : le cœur intercepte tout clic
 * sur un <a href> de la page pour le charger en AJAX (jeedomUtils.setJeedomMenu),
 * et un <button> se désactive proprement.
 *
 * Un terminal donne un shell sur la machine : la page est réservée aux
 * administrateurs, exactement comme la porte AJAX qui délivre le jeton.
 */
if (!isConnect('admin')) {
	throw new Exception('{{401 - Accès non autorisé}}');
}
?>

<div id="div_jeeterm" class="jeeterm-root">
	<div class="jeeterm-toolbar" role="toolbar" aria-label="{{Terminal}}">
		<div class="jeeterm-toolbar-row">
			<span id="span_jeetermStatus" class="label label-warning jeeterm-status" role="status" aria-live="polite">{{Connexion…}}</span>
			<!-- Titre posé par le shell (séquence OSC 0/2, par exemple user@hôte:dossier). -->
			<span id="span_jeetermTitle" class="jeeterm-title jeeterm-hide-xs"></span>

			<button type="button" id="bt_jeetermReconnect" class="btn btn-sm btn-success" style="display:none;" title="{{Ouvrir une nouvelle session (ou appuyez sur Entrée dans le terminal)}}">
				<i class="fas fa-plug" aria-hidden="true"></i> <span class="jeeterm-hide-xs">{{Reconnecter}}</span>
			</button>

			<div class="btn-group btn-group-sm jeeterm-hide-xs" role="group" aria-label="{{Taille de la police}}">
				<button type="button" id="bt_jeetermFontMinus" class="btn btn-sm btn-default" title="{{Réduire la police}}" aria-label="{{Réduire la police}}"><i class="fas fa-minus" aria-hidden="true"></i></button>
				<span class="btn btn-sm btn-default jeeterm-fontsize" title="{{Taille de la police}}"><i class="fas fa-font" aria-hidden="true"></i> <span id="span_jeetermFontSize">14</span></span>
				<button type="button" id="bt_jeetermFontPlus" class="btn btn-sm btn-default" title="{{Agrandir la police}}" aria-label="{{Agrandir la police}}"><i class="fas fa-plus" aria-hidden="true"></i></button>
			</div>

			<div class="btn-group btn-group-sm" role="group" aria-label="{{Presse-papiers}}">
				<button type="button" id="bt_jeetermCopy" class="btn btn-sm btn-default" title="{{Copier la sélection (Ctrl+Maj+C)}}" aria-label="{{Copier la sélection}}"><i class="far fa-copy" aria-hidden="true"></i> <span class="jeeterm-hide-xs">{{Copier}}</span></button>
				<button type="button" id="bt_jeetermPaste" class="btn btn-sm btn-default" title="{{Coller (Ctrl+Maj+V)}}" aria-label="{{Coller}}"><i class="fas fa-paste" aria-hidden="true"></i> <span class="jeeterm-hide-xs">{{Coller}}</span></button>
			</div>

			<div class="btn-group btn-group-sm jeeterm-hide-xs" role="group" aria-label="{{Session}}">
				<button type="button" id="bt_jeetermSave" class="btn btn-sm btn-default" title="{{Enregistrer la session dans un fichier texte}}" aria-label="{{Enregistrer la session}}"><i class="fas fa-download" aria-hidden="true"></i></button>
				<button type="button" id="bt_jeetermClear" class="btn btn-sm btn-default" title="{{Effacer l'écran et l'historique}}" aria-label="{{Effacer l'écran}}"><i class="fas fa-eraser" aria-hidden="true"></i></button>
			</div>

			<div class="jeeterm-toolbar-end">
				<button type="button" id="bt_jeetermSnippets" class="btn btn-sm btn-default" title="{{Commandes rapides}}" aria-label="{{Commandes rapides}}" aria-haspopup="true" aria-expanded="false" aria-controls="div_jeetermSnippets"><i class="fas fa-bolt" aria-hidden="true"></i> <span class="jeeterm-hide-xs">{{Commandes}}</span></button>
				<button type="button" id="bt_jeetermSearch" class="btn btn-sm btn-default" title="{{Rechercher (Ctrl+Maj+F)}}" aria-label="{{Rechercher}}" aria-pressed="false"><i class="fas fa-search" aria-hidden="true"></i></button>
				<button type="button" id="bt_jeetermToggleKeys" class="btn btn-sm btn-default" title="{{Afficher ou masquer les touches spéciales}}" aria-label="{{Touches spéciales}}" aria-pressed="false"><i class="fas fa-keyboard" aria-hidden="true"></i></button>
				<button type="button" id="bt_jeetermFullscreen" class="btn btn-sm btn-default" title="{{Plein écran}}" aria-label="{{Plein écran}}"><i class="fas fa-expand" aria-hidden="true"></i></button>
				<button type="button" id="bt_jeetermSettings" class="btn btn-sm btn-default" title="{{Réglages du terminal (thème, police, curseur…)}}" aria-label="{{Réglages du terminal}}" aria-haspopup="true" aria-expanded="false" aria-controls="div_jeetermSettings"><i class="fas fa-cog" aria-hidden="true"></i></button>
				<button type="button" id="bt_jeetermConfig" class="btn btn-sm btn-default jeeterm-hide-xs" title="{{Configuration du plugin}}" aria-label="{{Configuration du plugin}}"><i class="fas fa-wrench" aria-hidden="true"></i></button>
			</div>
		</div>

		<!-- Recherche dans le terminal (module xterm « search »). -->
		<div id="div_jeetermSearch" class="jeeterm-toolbar-row jeeterm-search" role="search" style="display:none;">
			<input type="text" id="in_jeetermFind" class="form-control input-sm jeeterm-search-input" placeholder="{{Rechercher dans le terminal}}" aria-label="{{Rechercher dans le terminal}}" spellcheck="false" autocomplete="off" autocapitalize="off">
			<span id="span_jeetermSearchCount" class="jeeterm-search-count" aria-live="polite"></span>
			<div class="btn-group btn-group-sm" role="group">
				<button type="button" id="bt_jeetermFindPrev" class="btn btn-sm btn-default" title="{{Occurrence précédente (Maj+Entrée)}}" aria-label="{{Occurrence précédente}}"><i class="fas fa-chevron-up" aria-hidden="true"></i></button>
				<button type="button" id="bt_jeetermFindNext" class="btn btn-sm btn-default" title="{{Occurrence suivante (Entrée)}}" aria-label="{{Occurrence suivante}}"><i class="fas fa-chevron-down" aria-hidden="true"></i></button>
				<button type="button" id="bt_jeetermFindClose" class="btn btn-sm btn-default" title="{{Fermer la recherche (Échap)}}" aria-label="{{Fermer la recherche}}"><i class="fas fa-times" aria-hidden="true"></i></button>
			</div>
		</div>

		<!-- Touches absentes d'un clavier tactile. data-key est interprété par
		     jeeterm.js ; Ctrl et Alt sont collantes : elles s'appliquent à la
		     touche suivante, puis se relâchent. -->
		<div id="div_jeetermKeys" class="jeeterm-toolbar-row jeeterm-keys" role="group" aria-label="{{Touches spéciales}}" style="display:none;">
			<button type="button" class="btn btn-xs btn-default jeeterm-key" data-key="esc" title="{{Échap}}">{{Échap}}</button>
			<button type="button" class="btn btn-xs btn-default jeeterm-key" data-key="tab" title="{{Tabulation}}">Tab</button>
			<button type="button" class="btn btn-xs btn-default jeeterm-key jeeterm-modifier" data-key="ctrl" title="{{Ctrl pour la touche suivante}}" aria-pressed="false">Ctrl</button>
			<button type="button" class="btn btn-xs btn-default jeeterm-key jeeterm-modifier" data-key="alt" title="{{Alt pour la touche suivante}}" aria-pressed="false">Alt</button>
			<button type="button" class="btn btn-xs btn-default jeeterm-key" data-key="up" title="{{Flèche haut}}" aria-label="{{Flèche haut}}"><i class="fas fa-arrow-up" aria-hidden="true"></i></button>
			<button type="button" class="btn btn-xs btn-default jeeterm-key" data-key="down" title="{{Flèche bas}}" aria-label="{{Flèche bas}}"><i class="fas fa-arrow-down" aria-hidden="true"></i></button>
			<button type="button" class="btn btn-xs btn-default jeeterm-key" data-key="left" title="{{Flèche gauche}}" aria-label="{{Flèche gauche}}"><i class="fas fa-arrow-left" aria-hidden="true"></i></button>
			<button type="button" class="btn btn-xs btn-default jeeterm-key" data-key="right" title="{{Flèche droite}}" aria-label="{{Flèche droite}}"><i class="fas fa-arrow-right" aria-hidden="true"></i></button>
			<button type="button" class="btn btn-xs btn-default jeeterm-key" data-key="home" title="{{Début de ligne}}">{{Début}}</button>
			<button type="button" class="btn btn-xs btn-default jeeterm-key" data-key="end" title="{{Fin de ligne}}">{{Fin}}</button>
			<button type="button" class="btn btn-xs btn-default jeeterm-key" data-key="text" data-text="|">|</button>
			<button type="button" class="btn btn-xs btn-default jeeterm-key" data-key="text" data-text="~">~</button>
			<button type="button" class="btn btn-xs btn-default jeeterm-key" data-key="text" data-text="/">/</button>
			<button type="button" class="btn btn-xs btn-default jeeterm-key" data-key="text" data-text="-">-</button>
		</div>
	</div>

	<!-- Bandeau d'erreur : démon arrêté, accès refusé, proxy absent… Le texte
	     est posé par jeeterm.js en textContent, jamais en HTML. -->
	<div id="div_jeetermError" class="alert alert-danger jeeterm-error" role="alert" style="display:none;">
		<i class="fas fa-exclamation-triangle" aria-hidden="true"></i> <span id="span_jeetermError"></span>
		<div class="jeeterm-error-actions">
			<button type="button" id="bt_jeetermErrorConfig" class="btn btn-xs btn-default"><i class="fas fa-wrench" aria-hidden="true"></i> {{Configuration du plugin}}</button>
		</div>
	</div>

	<!-- Collage de repli : sur une page en http:// (hors localhost), le
	     navigateur interdit la lecture du presse-papiers par script. -->
	<div id="div_jeetermPaste" class="jeeterm-paste" style="display:none;">
		<label for="ta_jeetermPaste" class="jeeterm-paste-label">{{Collez le texte ici (Ctrl+V ou appui long), puis envoyez-le au terminal :}}</label>
		<textarea id="ta_jeetermPaste" class="form-control jeeterm-paste-text" rows="3" spellcheck="false" autocomplete="off" autocapitalize="off"></textarea>
		<div class="jeeterm-paste-actions">
			<button type="button" id="bt_jeetermPasteSend" class="btn btn-sm btn-success"><i class="fas fa-check" aria-hidden="true"></i> {{Envoyer (Ctrl+Entrée)}}</button>
			<button type="button" id="bt_jeetermPasteCancel" class="btn btn-sm btn-default"><i class="fas fa-times" aria-hidden="true"></i> {{Annuler (Échap)}}</button>
		</div>
	</div>

	<!-- Réglages du terminal : propres à ce navigateur (localStorage),
	     appliqués aussitôt, sans reconnexion. Panneau rangé dans le bloc
	     principal pour rester visible en plein écran. -->
	<div id="div_jeetermSettings" class="jeeterm-panel jeeterm-settings" role="dialog" aria-label="{{Réglages du terminal}}" style="display:none;">
		<div class="jeeterm-panel-title"><i class="fas fa-cog" aria-hidden="true"></i> {{Réglages du terminal}}</div>
		<div class="jeeterm-field">
			<label for="sel_jeetermTheme">{{Thème}}</label>
			<select id="sel_jeetermTheme" class="form-control input-sm" data-setting="theme">
				<option value="auto">{{Jeedom (automatique)}}</option>
				<option value="black">{{Noir}}</option>
				<option value="green">{{Phosphore vert}}</option>
				<option value="amber">{{Ambre}}</option>
				<option value="dracula">Dracula</option>
				<option value="solarized">{{Solarized sombre}}</option>
				<option value="monokai">Monokai</option>
				<option value="nord">Nord</option>
				<option value="tango">{{Tango clair}}</option>
			</select>
		</div>
		<div class="jeeterm-field jeeterm-check">
			<input type="checkbox" id="cb_jeetermCrt" data-setting="crt">
			<label for="cb_jeetermCrt">{{Effet écran cathodique rétro}}</label>
		</div>
		<div class="jeeterm-field">
			<label for="sel_jeetermFont">{{Police}}</label>
			<select id="sel_jeetermFont" class="form-control input-sm" data-setting="font">
				<option value="default">{{Système (par défaut)}}</option>
				<option value="dejavu">DejaVu Sans Mono</option>
				<option value="liberation">Liberation Mono</option>
				<option value="ubuntu">Ubuntu Mono</option>
				<option value="fira">Fira Code</option>
				<option value="jetbrains">JetBrains Mono</option>
				<option value="cascadia">Cascadia Code</option>
				<option value="source">Source Code Pro</option>
				<option value="hack">Hack</option>
				<option value="consolas">Consolas / Menlo</option>
				<option value="courier">Courier New</option>
			</select>
		</div>
		<div class="jeeterm-field jeeterm-inline">
			<span class="jeeterm-field-label">{{Taille}}</span>
			<div class="btn-group btn-group-sm" role="group" aria-label="{{Taille de la police}}">
				<button type="button" class="btn btn-sm btn-default" data-action="fontMinus" title="{{Réduire la police}}" aria-label="{{Réduire la police}}"><i class="fas fa-minus" aria-hidden="true"></i></button>
				<span class="btn btn-sm btn-default jeeterm-fontsize"><span id="span_jeetermFontSize2">14</span></span>
				<button type="button" class="btn btn-sm btn-default" data-action="fontPlus" title="{{Agrandir la police}}" aria-label="{{Agrandir la police}}"><i class="fas fa-plus" aria-hidden="true"></i></button>
			</div>
			<label for="sel_jeetermLineHeight" class="jeeterm-field-label">{{Interligne}}</label>
			<select id="sel_jeetermLineHeight" class="form-control input-sm jeeterm-narrow" data-setting="lineHeight">
				<option value="1">1</option>
				<option value="1.1">1,1</option>
				<option value="1.2">1,2</option>
				<option value="1.35">1,35</option>
			</select>
		</div>
		<div class="jeeterm-field jeeterm-inline">
			<label for="sel_jeetermCursor" class="jeeterm-field-label">{{Curseur}}</label>
			<select id="sel_jeetermCursor" class="form-control input-sm jeeterm-narrow" data-setting="cursorStyle">
				<option value="block">{{Bloc}}</option>
				<option value="bar">{{Barre}}</option>
				<option value="underline">{{Souligné}}</option>
			</select>
			<span class="jeeterm-check">
				<input type="checkbox" id="cb_jeetermBlink" data-setting="cursorBlink">
				<label for="cb_jeetermBlink">{{Clignotant}}</label>
			</span>
		</div>
		<div class="jeeterm-field jeeterm-inline">
			<label for="sel_jeetermBell" class="jeeterm-field-label">{{Cloche}}</label>
			<select id="sel_jeetermBell" class="form-control input-sm jeeterm-narrow" data-setting="bell">
				<option value="visual">{{Visuelle (éclair)}}</option>
				<option value="sound">{{Sonore (bip)}}</option>
				<option value="none">{{Aucune}}</option>
			</select>
		</div>
		<div class="jeeterm-field jeeterm-check">
			<input type="checkbox" id="cb_jeetermCopySel" data-setting="copyOnSelect">
			<label for="cb_jeetermCopySel">{{Copier dès la sélection (comme sous Linux)}}</label>
		</div>
		<!-- Sur petit écran, les boutons retirés de la barre sont ici. -->
		<div class="jeeterm-field jeeterm-xs-only jeeterm-panel-actions">
			<button type="button" class="btn btn-sm btn-default" data-action="save"><i class="fas fa-download" aria-hidden="true"></i> {{Enregistrer la session}}</button>
			<button type="button" class="btn btn-sm btn-default" data-action="clear"><i class="fas fa-eraser" aria-hidden="true"></i> {{Effacer l'écran}}</button>
			<button type="button" class="btn btn-sm btn-default" data-action="config"><i class="fas fa-wrench" aria-hidden="true"></i> {{Configuration du plugin}}</button>
		</div>
		<div class="jeeterm-panel-foot">
			<span class="jeeterm-hint">{{Réglages propres à ce navigateur.}}</span>
			<button type="button" class="btn btn-xs btn-default" data-action="resetSettings"><i class="fas fa-undo" aria-hidden="true"></i> {{Par défaut}}</button>
		</div>
	</div>

	<!-- Commandes rapides : tapées dans le terminal sans Entrée (sauf si la
	     commande est marquée « exécuter »). Liste construite par jeeterm.js,
	     textes posés en textContent. -->
	<div id="div_jeetermSnippets" class="jeeterm-panel jeeterm-snippets" role="dialog" aria-label="{{Commandes rapides}}" style="display:none;">
		<div class="jeeterm-panel-title"><i class="fas fa-bolt" aria-hidden="true"></i> {{Commandes rapides}}</div>
		<div class="jeeterm-hint">{{La commande est tapée dans le terminal : vérifiez-la, puis appuyez sur Entrée.}}</div>
		<ul id="ul_jeetermSnippets" class="jeeterm-snippet-list"></ul>
		<div id="div_jeetermSnippetForm" class="jeeterm-snippet-form" style="display:none;">
			<input type="text" id="in_jeetermSnippetLabel" class="form-control input-sm" placeholder="{{Libellé (facultatif)}}" aria-label="{{Libellé}}" maxlength="60" spellcheck="false" autocomplete="off">
			<input type="text" id="in_jeetermSnippetCmd" class="form-control input-sm jeeterm-mono" placeholder="{{Commande}}" aria-label="{{Commande}}" maxlength="1000" spellcheck="false" autocomplete="off" autocapitalize="off">
			<span class="jeeterm-check">
				<input type="checkbox" id="cb_jeetermSnippetRun">
				<label for="cb_jeetermSnippetRun">{{Exécuter aussitôt (ajoute Entrée)}}</label>
			</span>
			<div class="jeeterm-panel-actions">
				<button type="button" class="btn btn-xs btn-success" data-action="snippetSave"><i class="fas fa-check" aria-hidden="true"></i> {{Ajouter}}</button>
				<button type="button" class="btn btn-xs btn-default" data-action="snippetCancel"><i class="fas fa-times" aria-hidden="true"></i> {{Annuler}}</button>
			</div>
		</div>
		<div class="jeeterm-panel-foot">
			<button type="button" class="btn btn-xs btn-default" data-action="snippetAdd"><i class="fas fa-plus" aria-hidden="true"></i> {{Ajouter une commande}}</button>
			<button type="button" class="btn btn-xs btn-default" data-action="snippetReset" title="{{Remettre la liste d'origine}}"><i class="fas fa-undo" aria-hidden="true"></i> {{Liste d'origine}}</button>
		</div>
	</div>

	<!-- Calques décoratifs posés au-dessus du terminal, transparents à la
	     souris (pointer-events: none) : la sélection et le calcul de taille
	     n'en voient rien. -->
	<div id="div_jeetermTerminal" class="jeeterm-terminal">
		<div class="jeeterm-crt-layer" aria-hidden="true"></div>
		<div class="jeeterm-bell-layer" aria-hidden="true"></div>
		<div id="div_jeetermHud" class="jeeterm-hud" aria-hidden="true"></div>
	</div>
</div>

<?php
/* Ordre imposé : xterm, puis ses modules, puis le script de la page.
 * domUtils.loadScript() (core/dom/dom.utils.js) les exécute dans cet ordre en
 * attendant chaque chargement ; les fichiers dont le chemin contient
 * « /3rdparty/ » ne sont injectés qu'une fois dans <head> et restent chargés
 * d'une page à l'autre. jeeterm.js, lui, passe par core/php/getResource.php,
 * qui traduit ses chaînes entre doubles accolades, et il est ré-exécuté à
 * chaque affichage. */
include_file('3rdparty', 'xterm/xterm', 'css', 'jeeterm');
include_file('desktop', 'jeeterm', 'css', 'jeeterm');
include_file('3rdparty', 'xterm/xterm', 'js', 'jeeterm');
include_file('3rdparty', 'xterm/addon-fit', 'js', 'jeeterm');
include_file('3rdparty', 'xterm/addon-web-links', 'js', 'jeeterm');
include_file('3rdparty', 'xterm/addon-search', 'js', 'jeeterm');
include_file('3rdparty', 'xterm/addon-unicode11', 'js', 'jeeterm');
include_file('desktop', 'jeeterm', 'js', 'jeeterm');
?>
